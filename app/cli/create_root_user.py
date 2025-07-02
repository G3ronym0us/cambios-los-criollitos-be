import sys
from sqlalchemy.orm import Session
from app.database.connection import SessionLocal
from app.core.security import get_password_hash
from app.core.config import settings
from app.repositories.user_repository import UserRepository
from app.models.user import User
from app.enums.user_roles import UserRole
from datetime import datetime
from app.schemas.auth import AdminCreateUser

class RootUserManager:
    def __init__(self):
        self.db = SessionLocal()
        self.user_repo = UserRepository(self.db)
    
    def create_root_user(self) -> User:
        """Crear usuario root usando configuración del entorno."""
        
        # Obtener credenciales del settings (que viene del .env)
        root_email = settings.ROOT_USER_EMAIL
        root_password = settings.ROOT_USER_PASSWORD
        root_name = settings.ROOT_USER_NAME
        
        if not root_email or not root_password:
            raise ValueError(
                "ROOT_USER_EMAIL and ROOT_USER_PASSWORD must be set in .env file"
            )
        
        # Verificar si el usuario ya existe
        existing_user = self.user_repo.get_by_email(root_email)
        if existing_user:
            if existing_user.role == UserRole.ROOT:
                print(f"✅ Root user already exists: {root_email}")
                return existing_user
            else:
                # Actualizar usuario existente a ROOT
                update_data = {
                    "role": UserRole.ROOT,
                    "is_verified": True
                }
                # Solo actualizar is_active si el campo existe
                if hasattr(existing_user, 'is_active'):
                    update_data["is_active"] = True
                
                updated_user = self.user_repo.update(existing_user.id, update_data)
                print(f"✅ Updated existing user to ROOT: {root_email}")
                return updated_user
        
        # Crear nuevo usuario root
        root_user_data = AdminCreateUser(   
            username=root_email.split("@")[0],
            email=root_email,
            password=root_password,
            full_name=root_name,
            role_name=UserRole.ROOT.value,
            is_active=True,
            is_verified=True,
        )
        
        # Solo agregar is_active si el modelo lo soporta
        # (verificaremos esto cuando veamos el modelo)
        # root_user_data["is_active"] = True
        root_user = self.user_repo.create_user(root_user_data)
        print(f"✅ Root user created successfully: {root_email}")
        
        return root_user
    
    def list_root_users(self):
        """Listar todos los usuarios con rol ROOT."""
        # Verificar si el método existe
        if not hasattr(self.user_repo, 'get_by_role'):
            print("❌ UserRepository doesn't have get_by_role method")
            print("💡 You'll need to add this method to the repository")
            return
        
        root_users = self.user_repo.get_by_role(UserRole.ROOT)
        
        if not root_users:
            print("❌ No root users found")
            return
        
        print("\n🔑 Root Users:")
        print("-" * 50)
        for user in root_users:
            is_active = getattr(user, 'is_active', 'Unknown')
            is_verified = getattr(user, 'is_verified', 'Unknown')
            
            status = f"✅ Active" if is_active else f"❌ Inactive" if is_active is False else "❓ Unknown"
            verified = f"✅ Verified" if is_verified else f"❌ Not Verified" if is_verified is False else "❓ Unknown"
            
            print(f"ID: {user.id}")
            print(f"Email: {user.email}")
            print(f"Name: {user.full_name}")
            print(f"Status: {status}")
            print(f"Verified: {verified}")
            print(f"Created: {getattr(user, 'created_at', 'Unknown')}")
            print("-" * 50)
    
    def __del__(self):
        if hasattr(self, 'db'):
            self.db.close()

def main():
    """Función principal del CLI."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Root User Management CLI")
    parser.add_argument("action", choices=["create", "list"], 
                       help="Action to perform")
    
    args = parser.parse_args()
    
    try:
        manager = RootUserManager()
        
        if args.action == "create":
            manager.create_root_user()
        elif args.action == "list":
            manager.list_root_users()
    
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        print("\n💡 Make sure your .env file has:")
        print("   ROOT_USER_EMAIL=admin@yourdomain.com")
        print("   ROOT_USER_PASSWORD=your_secure_password")
        sys.exit(1)

if __name__ == "__main__":
    main()