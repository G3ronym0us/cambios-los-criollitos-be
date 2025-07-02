from enum import Enum

class UserRole(Enum):
    USER = "user"
    MODERATOR = "moderator"
    ROOT = "root"

    @property
    def level(self) -> int:
        """Nivel jerárquico del rol (mayor número = más privilegios)"""
        hierarchy = {
            UserRole.USER: 1,
            UserRole.MODERATOR: 2,
            UserRole.ROOT: 3
        }
        return hierarchy[self]
    
    def can_manage(self, other_role: 'UserRole') -> bool:
        """Verificar si este rol puede gestionar otro rol"""
        return self.level > other_role.level
    
    def has_permission(self, required_role: 'UserRole') -> bool:
        """Verificar si tiene los permisos requeridos"""
        return self.level >= required_role.level

    @classmethod
    def get_manageable_roles(cls, current_role: 'UserRole') -> list:
        """Obtener roles que puede gestionar el rol actual"""
        return [role for role in cls if current_role.can_manage(role)]
    
    def __str__(self):
        return self.value
    
    def __repr__(self):
        return f"UserRole.{self.name}"
