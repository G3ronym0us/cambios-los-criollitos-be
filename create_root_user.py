import os
import sys
sys.path.append(os.path.dirname(__file__))

def main():
    print("🚀 Creating Root User...")
    print("=" * 50)
    
    try:
        from app.cli.create_root_user import RootUserManager
        
        manager = RootUserManager()
        manager.create_root_user()
        print("=" * 50)
        print("✅ Root user setup completed!")
        
    except ImportError as e:
        print(f"❌ Import Error: {str(e)}")
        print("💡 Make sure you're in the backend directory and have all dependencies installed")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error creating root user: {str(e)}")
        print("\n💡 Make sure to set these environment variables in your .env file:")
        print("   ROOT_USER_EMAIL=admin@yourdomain.com")
        print("   ROOT_USER_PASSWORD=your_secure_password")
        sys.exit(1)

if __name__ == "__main__":
    main()