from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional, List
from datetime import datetime
from app.models.user import User
from app.schemas.auth import UserRegister, UserUpdate, AdminCreateUser
from app.core.security import get_password_hash, verify_password
from app.enums.user_roles import UserRole

class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_user(self, user_data: UserRegister, role: UserRole = UserRole.USER) -> User:
        """Crear nuevo usuario"""
        print(f"Creating user with data: {user_data}")
        hashed_password = get_password_hash(user_data.password)
        print(f"Hashed password: {hashed_password}")
        
        db_user = User(
            username=user_data.username,
            email=user_data.email,
            full_name=user_data.full_name,
            phone_number=user_data.phone_number,
            hashed_password=hashed_password,
            role=role,
            is_active=True,
            is_verified=False,
            login_count=0,
            failed_login_attempts=0
        )
        
        self.db.add(db_user)
        self.db.commit()
        self.db.refresh(db_user)
        return db_user

    def admin_create_user(self, user_data: AdminCreateUser) -> User:
        """Crear usuario como administrador"""
        # Convertir string a enum
        try:
            role = UserRole(user_data.role_name)
        except ValueError:
            role = UserRole.USER
        
        hashed_password = get_password_hash(user_data.password)
        
        db_user = User(
            username=user_data.username,
            email=user_data.email,
            full_name=user_data.full_name,
            phone_number=user_data.phone_number,
            hashed_password=hashed_password,
            role=role,
            is_active=user_data.is_active,
            is_verified=user_data.is_verified,
            login_count=0,
            failed_login_attempts=0
        )
        
        self.db.add(db_user)
        self.db.commit()
        self.db.refresh(db_user)
        return db_user

    def change_user_role(self, user_id: int, new_role: UserRole) -> bool:
        """Cambiar rol de un usuario"""
        user = self.get_by_id(user_id)
        if not user:
            return False
        
        user.role = new_role
        user.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def get_users_by_role(self, role: UserRole) -> List[User]:
        """Obtener usuarios por rol"""
        return self.db.query(User).filter(User.role == role).all()

    # ... resto de métodos sin cambios
    def get_by_id(self, user_id: int) -> Optional[User]:
        return self.db.query(User).filter(User.id == user_id).first()

    def get_by_email(self, email: str) -> Optional[User]:
        return self.db.query(User).filter(User.email == email).first()

    def get_by_username(self, username: str) -> Optional[User]:
        return self.db.query(User).filter(User.username == username).first()

    def get_by_username_or_email(self, username_or_email: str) -> Optional[User]:
        return self.db.query(User).filter(
            or_(User.username == username_or_email, User.email == username_or_email)
        ).first()

    def get_all_users(self, skip: int = 0, limit: int = 100) -> List[User]:
        return self.db.query(User).offset(skip).limit(limit).all()

    def authenticate_user(self, username_or_email: str, password: str) -> Optional[User]:
        user = self.get_by_username_or_email(username_or_email)
        if not user:
            return None
        
        if user.locked_until and user.locked_until > datetime.utcnow():
            return None
        
        if not verify_password(password, user.hashed_password):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= 5:
                from datetime import timedelta
                user.locked_until = datetime.utcnow() + timedelta(minutes=15)
            self.db.commit()
            return None
        
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login = datetime.utcnow()
        user.login_count += 1
        self.db.commit()
        
        return user

    def update_user(self, user_id: int, user_data: UserUpdate) -> Optional[User]:
        user = self.get_by_id(user_id)
        if not user:
            return None
        
        update_data = user_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(user, field, value)
        
        user.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(user)
        return user

    def change_password(self, user_id: int, current_password: str, new_password: str) -> bool:
        user = self.get_by_id(user_id)
        if not user:
            return False
        
        if not verify_password(current_password, user.hashed_password):
            return False
        
        user.hashed_password = get_password_hash(new_password)
        user.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def deactivate_user(self, user_id: int) -> bool:
        user = self.get_by_id(user_id)
        if not user:
            return False
        
        user.is_active = False
        user.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def username_exists(self, username: str) -> bool:
        return self.db.query(User).filter(User.username == username).first() is not None

    def email_exists(self, email: str) -> bool:
        return self.db.query(User).filter(User.email == email).first() is not None
