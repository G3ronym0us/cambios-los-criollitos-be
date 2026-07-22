from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID

from app.database.connection import get_db
from app.schemas.user import (
    UserCreate, UserUpdate, UserResponse,
    CommissionUserUpdate, CommissionUserResponse, CommissionUserList
)
from app.repositories.user_repository import UserRepository
from app.models.user import User
from app.core.dependencies import get_current_user, get_moderator_user, get_root_user

router = APIRouter(prefix="/users", tags=["Users"])


# ===== User CRUD =====

@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)  # Solo moderadores pueden crear usuarios
):
    """
    Crear nuevo usuario

    **Requiere**: Permisos de moderador o superior

    **Ejemplo**:
    ```json
    {
      "username": "nuevo_usuario",
      "email": "usuario@example.com",
      "full_name": "Nuevo Usuario",
      "password": "password123",
      "role": "user"
    }
    ```

    **Roles disponibles**: user, moderator, root
    """
    user_repo = UserRepository(db)

    # Verificar si el username ya existe
    if user_repo.username_exists(user_data.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Username '{user_data.username}' already exists"
        )

    # Verificar si el email ya existe
    if user_repo.email_exists(user_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Email '{user_data.email}' already exists"
        )

    # El número de WhatsApp es único entre usuarios.
    if user_data.phone_number:
        owner = user_repo.get_by_whatsapp_phone(user_data.phone_number)
        if owner:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Ese número de WhatsApp ya es de {owner.full_name or owner.username}",
            )

    # Crear usuario usando admin_create_user
    from app.schemas.auth import AdminCreateUser
    from app.enums.user_roles import UserRole

    admin_user_data = AdminCreateUser(
        username=user_data.username,
        email=user_data.email,
        full_name=user_data.full_name,
        password=user_data.password,
        role_name=user_data.role.upper() if user_data.role else "USER",
        is_active=user_data.is_active if user_data.is_active is not None else True,
        is_verified=False,
        phone_number=user_data.phone_number,
    )

    new_user = user_repo.admin_create_user(admin_user_data)

    return UserResponse(**new_user.dict())


@router.put("/{user_uuid}", response_model=UserResponse)
async def update_user(
    user_uuid: UUID,
    user_data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)  # Solo moderadores
):
    """
    Actualizar usuario completo

    **Requiere**: Permisos de moderador o superior

    **Ejemplo**:
    ```json
    {
      "full_name": "Nombre Actualizado",
      "email": "nuevo@example.com",
      "is_active": true,
      "phone_number": "+58412345678",
      "bio": "Operador de cambio"
    }
    ```

    **Nota**: Para actualizar contraseña, usar el endpoint de cambio de contraseña
    """
    user_repo = UserRepository(db)

    # Verificar que el usuario existe
    user = user_repo.get_by_uuid(user_uuid)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with UUID {user_uuid} not found"
        )

    # Si se actualiza username, verificar que no exista
    if user_data.username and user_data.username != user.username:
        if user_repo.username_exists(user_data.username):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Username '{user_data.username}' already exists"
            )

    # Si se actualiza email, verificar que no exista
    if user_data.email and user_data.email != user.email:
        if user_repo.email_exists(str(user_data.email)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Email '{user_data.email}' already exists"
            )

    # El número de WhatsApp es único entre usuarios (sus membresías lo heredan). Si ya es
    # de otro usuario, avisar de quién para que el operador lo corrija.
    if user_data.phone_number:
        owner = user_repo.get_by_whatsapp_phone(user_data.phone_number, exclude_user_id=user.id)
        if owner:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Ese número de WhatsApp ya es de {owner.full_name or owner.username}",
            )

    # Si se actualiza el rol, convertir a enum
    if user_data.role:
        from app.enums.user_roles import UserRole
        try:
            new_role = UserRole(user_data.role)
            # Actualizar rol separadamente
            user_repo.change_user_role(user.id, new_role)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role: {user_data.role}. Valid roles: user, moderator, root"
            )

    # Actualizar el resto de campos
    updated_user = user_repo.update_user(user.id, user_data)

    if not updated_user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user"
        )

    return UserResponse(**updated_user.dict())


@router.delete("/{user_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user)  # Solo ROOT puede eliminar usuarios
):
    """
    Desactivar usuario (soft delete)

    **Requiere**: Permisos de ROOT

    **Nota**: No se eliminan usuarios físicamente, solo se desactivan (is_active=False)
    """
    user_repo = UserRepository(db)

    # Verificar que el usuario existe
    user = user_repo.get_by_uuid(user_uuid)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with UUID {user_uuid} not found"
        )

    # No permitir auto-eliminación
    if user_uuid == current_user.uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account"
        )

    # Desactivar usuario
    success = user_repo.deactivate_user(user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to deactivate user"
        )


# ===== Commission User Management =====

@router.get("/commission-users", response_model=CommissionUserList)
async def get_commission_users(
    page: int = Query(1, ge=1, description="Número de página"),
    per_page: int = Query(50, ge=1, le=100, description="Usuarios por página"),
    only_active: bool = Query(True, description="Solo usuarios activos"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Obtener lista de usuarios que pueden recibir comisiones

    **Uso**: Para listar todos los comisionistas configurados en el sistema
    """
    user_repo = UserRepository(db)

    skip = (page - 1) * per_page
    users, total = user_repo.get_commission_users(skip=skip, limit=per_page, only_active=only_active)

    total_pages = (total + per_page - 1) // per_page

    return CommissionUserList(
        users=[CommissionUserResponse(**user.dict()) for user in users],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages
    )


@router.get("/commission-users/available", response_model=List[CommissionUserResponse])
async def get_available_commission_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Obtener lista simple de usuarios disponibles para recibir comisiones

    **Uso**: Para dropdowns/selectores al crear transacciones

    **Retorna**: Solo usuarios activos que pueden recibir comisiones, ordenados por username
    """
    user_repo = UserRepository(db)
    users = user_repo.get_available_commission_users()

    return [CommissionUserResponse(**user.dict()) for user in users]


@router.put("/{user_uuid}/commission-settings", response_model=UserResponse)
async def update_commission_settings(
    user_uuid: UUID,
    commission_data: CommissionUserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)  # Solo moderadores o admins
):
    """
    Actualizar configuración de comisiones de un usuario

    **Requiere**: Permisos de moderador o superior

    **Ejemplo**:
    ```json
    {
      "can_receive_commission": true
    }
    ```
    """
    user_repo = UserRepository(db)

    # Verificar que el usuario existe
    user = user_repo.get_by_uuid(user_uuid)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with UUID {user_uuid} not found"
        )

    # Actualizar configuración
    updated_user = user_repo.update_commission_settings(
        user_id=user.id,
        can_receive_commission=commission_data.can_receive_commission
    )

    if not updated_user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update commission settings"
        )

    return UserResponse(**updated_user.dict())


@router.get("/{user_uuid}", response_model=UserResponse)
async def get_user(
    user_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Obtener información de un usuario por UUID

    **Permisos**:
    - Usuarios pueden ver su propia información
    - Moderadores/Admins pueden ver cualquier usuario
    """
    user_repo = UserRepository(db)
    user = user_repo.get_by_uuid(user_uuid)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with UUID {user_uuid} not found"
        )

    # Solo admins pueden ver información de otros usuarios
    if current_user.uuid != user_uuid and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own information"
        )

    return UserResponse(**user.dict())


@router.get("", response_model=List[UserResponse])
async def get_all_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)  # Solo moderadores
):
    """
    Obtener lista de todos los usuarios

    **Requiere**: Permisos de moderador o superior
    """
    user_repo = UserRepository(db)
    users = user_repo.get_all_users(skip=skip, limit=limit)

    return [UserResponse(**user.dict()) for user in users]
