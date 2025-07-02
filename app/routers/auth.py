from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import timedelta
from typing import List

from app.database.connection import get_db
from app.schemas.auth import (
    UserRegister, UserLogin, UserResponse, Token, RefreshToken,
    ChangePassword, UserUpdate, UserRoleUpdate, AdminCreateUser
)
from app.repositories.user_repository import UserRepository
from app.core.security import create_access_token, create_refresh_token, verify_token
from app.core.config import settings
from app.core.dependencies import (
    get_current_active_user, get_root_user, get_moderator_user
)
from app.models.user import User
from app.enums.user_roles import UserRole

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/register", response_model=UserResponse)
async def register(user_data: UserRegister, db: Session = Depends(get_db)):
    """Registrar nuevo usuario"""
    user_repo = UserRepository(db)
    
    # Verificar si username o email ya existen
    if user_repo.username_exists(user_data.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username ya está en uso"
        )
    
    if user_repo.email_exists(user_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email ya está registrado"
        )
    
    # Crear usuario con rol por defecto
    user = user_repo.create_user(user_data, UserRole.USER.value)
    
    return UserResponse(**user.dict())

@router.post("/login", response_model=Token)
async def login(
    response: Response,
    user_credentials: UserLogin,
    db: Session = Depends(get_db)
):
    """Iniciar sesión"""
    user_repo = UserRepository(db)
    
    # Autenticar usuario
    user = user_repo.authenticate_user(
        user_credentials.username_or_email,
        user_credentials.password
    )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Crear tokens
    access_token_expires = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username},
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(data={"sub": str(user.id)})
    
    # Configurar cookie segura
    response.set_cookie(
        key=settings.COOKIE_NAME,
        value=access_token,
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=settings.COOKIE_HTTPONLY,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE
    )
    
    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse(**user.dict())
    )

@router.post("/logout")
async def logout(response: Response):
    """Cerrar sesión"""
    response.delete_cookie(
        key=settings.COOKIE_NAME,
        httponly=settings.COOKIE_HTTPONLY,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE
    )
    return {"message": "Sesión cerrada exitosamente"}

@router.post("/refresh", response_model=Token)
async def refresh_token(
    refresh_data: RefreshToken,
    response: Response,
    db: Session = Depends(get_db)
):
    """Renovar token de acceso"""
    # Verificar refresh token
    payload = verify_token(refresh_data.refresh_token, "refresh")
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token inválido"
        )
    
    user_id = int(payload.get("sub"))
    user_repo = UserRepository(db)
    user = user_repo.get_by_id(user_id)
    
    if not user or not user.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o inactivo"
        )
    
    # Crear nuevos tokens
    access_token_expires = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username},
        expires_delta=access_token_expires
    )
    new_refresh_token = create_refresh_token(data={"sub": str(user.id)})
    
    # Actualizar cookie
    response.set_cookie(
        key=settings.COOKIE_NAME,
        value=access_token,
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=settings.COOKIE_HTTPONLY,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE
    )

    return Token(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse(**user.dict())
    )

@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_active_user)):
    """Obtener información del usuario actual"""
    return UserResponse(**current_user.dict())

@router.put("/me", response_model=UserResponse)
async def update_current_user(
    user_data: UserUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Actualizar información del usuario actual"""
    user_repo = UserRepository(db)
    updated_user = user_repo.update_user(current_user.id, user_data)
    if not updated_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado"
        )

    return UserResponse(**updated_user.dict())

@router.post("/change-password")
async def change_password(
    password_data: ChangePassword,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Cambiar contraseña del usuario actual"""
    user_repo = UserRepository(db)
    success = user_repo.change_password(
        current_user.id,
        password_data.current_password,
        password_data.new_password
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Contraseña actual incorrecta"
        )

    return {"message": "Contraseña actualizada exitosamente"}

@router.get("/users", response_model=List[UserResponse])
async def get_all_users(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_moderator_user),
    db: Session = Depends(get_db)
):
    """Obtener lista de usuarios (solo moderadores y root)"""
    user_repo = UserRepository(db)
    users = user_repo.get_all_users(skip=skip, limit=limit)
    return [UserResponse(**user.dict()) for user in users]

@router.post("/admin/create-user", response_model=UserResponse)
async def admin_create_user(
    user_data: AdminCreateUser,
    current_user: User = Depends(get_moderator_user),  # Puede crear usuarios si puede gestionar el rol
    db: Session = Depends(get_db)
):
    """Crear usuario como administrador"""
    user_repo = UserRepository(db)
    # Verificar que puede gestionar el rol solicitado
    target_role = UserRole(user_data.role_name)
    if not target_role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rol no válido"
        )

    if not current_user.role.can_manage_role(target_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No puedes crear usuarios con rol {target_role.display_name}"
        )

    # Verificar unicidad
    if user_repo.username_exists(user_data.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username ya está en uso"
        )

    if user_repo.email_exists(user_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email ya está registrado"
        )

    # Crear usuario
    user = user_repo.admin_create_user(user_data, target_role.id)
    return UserResponse(**user.dict())

@router.put("/admin/users/{user_id}/role")
async def change_user_role(
    user_id: int,
    role_data: UserRoleUpdate,
    current_user: User = Depends(get_moderator_user),
    db: Session = Depends(get_db)
):
    """Cambiar rol de un usuario"""
    user_repo = UserRepository(db)
    # Obtener usuario objetivo
    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado"
        )

    # Verificar que puede gestionar el usuario actual
    if not current_user.can_manage_user(target_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No puedes gestionar este usuario"
        )

    # Verificar que puede asignar el nuevo rol
    new_role = UserRole(role_data.role_name)
    if not new_role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rol no válido"
        )

    if not current_user.role.can_manage_role(new_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No puedes asignar el rol {new_role.display_name}"
        )

    # Cambiar rol
    success = user_repo.assign_role_to_user(user_id, role_data.role_name)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Error al cambiar rol"
        )

    return {"message": f"Rol cambiado a {new_role.display_name} exitosamente"}

@router.delete("/admin/users/{user_id}")
async def deactivate_user(
    user_id: int,
    current_user: User = Depends(get_moderator_user),
    db: Session = Depends(get_db)
):
    """Desactivar usuario"""
    user_repo = UserRepository(db)
    # Obtener usuario objetivo
    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado"
        )

    # Verificar que puede gestionar el usuario
    if not current_user.can_manage_user(target_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No puedes desactivar este usuario"
        )

    # No permitir auto-desactivación
    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes desactivarte a ti mismo"
        )

    success = user_repo.deactivate_user(user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Error al desactivar usuario"
        )

    return {"message": "Usuario desactivado exitosamente"}

@router.get("/admin/roles")
async def get_roles(
    current_user: User = Depends(get_moderator_user),
    db: Session = Depends(get_db)
):
    """Obtener lista de roles disponibles"""
    # Solo mostrar roles que puede gestionar
    if current_user.is_root:
        roles = UserRole.get_all_roles()
    else:
        roles = UserRole.get_manageable_roles(current_user.role)

    return [
        {
            "name": role.name,
            "display_name": role.display_name,
            "description": role.description,
            "level": role.level,
            "permissions": [f"{p.resource}:{p.action}" for p in role.permissions]
        }
        for role in roles
    ]

@router.post("/admin/init-roles")
async def initialize_roles(
    current_user: User = Depends(get_root_user),
    db: Session = Depends(get_db)
):
    """Inicializar roles y permisos por defecto (solo ROOT)"""
    UserRole.create_default_roles_and_permissions()
    return {"message": "Roles y permisos inicializados exitosamente"}

