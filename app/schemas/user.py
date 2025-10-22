from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Optional, List
from uuid import UUID

class UserBase(BaseModel):
    email: EmailStr
    username: str
    full_name: Optional[str] = None
    is_active: Optional[bool] = True

class UserCreate(UserBase):
    password: str
    role: Optional[str] = "user"

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
    role: Optional[str] = None
    phone_number: Optional[str] = None
    bio: Optional[str] = None

class UserResponse(UserBase):
    uuid: UUID
    role: Optional[str] = None
    role_display: Optional[str] = None
    is_verified: bool
    can_receive_commission: bool
    phone_number: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    last_login: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ===== Commission User Schemas =====

class CommissionUserUpdate(BaseModel):
    """Schema para actualizar configuración de comisiones de un usuario"""
    can_receive_commission: bool = Field(..., description="Si el usuario puede recibir comisiones")

class CommissionUserResponse(BaseModel):
    """Schema simplificado para listar usuarios comisionistas"""
    uuid: UUID
    username: str
    full_name: Optional[str] = None
    email: str
    can_receive_commission: bool
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

class CommissionUserList(BaseModel):
    """Lista paginada de usuarios comisionistas"""
    users: List[CommissionUserResponse]
    total: int
    page: int
    per_page: int
    total_pages: int
