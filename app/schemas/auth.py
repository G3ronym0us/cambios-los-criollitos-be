from pydantic import BaseModel, EmailStr, validator
from typing import Optional, List
from datetime import datetime

class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    
    @validator('username')
    def validate_username(cls, v):
        if len(v) < 3:
            raise ValueError('Username debe tener al menos 3 caracteres')
        if len(v) > 20:
            raise ValueError('Username no puede tener más de 20 caracteres')
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Username solo puede contener letras, números, guiones y guiones bajos')
        return v
    
    @validator('password')
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError('Password debe tener al menos 6 caracteres')
        return v

class UserLogin(BaseModel):
    username_or_email: str
    password: str

class RoleResponse(BaseModel):
    name: str
    display_name: str
    description: Optional[str]
    level: int

    class Config:
        from_attributes = True

class PermissionResponse(BaseModel):
    resource: str
    action: str
    description: Optional[str]

    class Config:
        from_attributes = True

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str] = None
    is_active: bool
    is_verified: bool
    role: Optional[str] = None
    role_display: Optional[str] = None
    permissions: List[str] = []
    last_login: Optional[datetime] = None
    created_at: datetime
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    phone_number: Optional[str] = None

    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    bio: Optional[str] = None
    phone_number: Optional[str] = None
    avatar_url: Optional[str] = None

class UserRoleUpdate(BaseModel):
    user_id: int
    role_name: str
    
    @validator('role_name')
    def validate_role_name(cls, v):
        allowed_roles = ["user", "moderator", "root"]
        if v not in allowed_roles:
            raise ValueError(f'Rol debe ser uno de: {", ".join(allowed_roles)}')
        return v

class ChangePassword(BaseModel):
    current_password: str
    new_password: str
    
    @validator('new_password')
    def validate_new_password(cls, v):
        if len(v) < 6:
            raise ValueError('Password debe tener al menos 6 caracteres')
        return v

class AdminCreateUser(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    role_name: str = "user"
    is_active: bool = True
    is_verified: bool = False
    
    @validator('role_name')
    def validate_role_name(cls, v):
        allowed_roles = ["user", "moderator", "root"]
        if v not in allowed_roles:
            raise ValueError(f'Rol debe ser uno de: {", ".join(allowed_roles)}')
        return v

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse

class TokenData(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None

class RefreshToken(BaseModel):
    refresh_token: str

class UserStats(BaseModel):
    total_users: int
    active_users: int
    verified_users: int
    users_by_role: dict
    recent_registrations: int

class SystemInfo(BaseModel):
    version: str
    total_users: int
    total_transactions: int
    total_rates: int
    last_scraping: Optional[datetime]
    system_status: str
