from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# URL de la base de datos
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tasas_user:tasas_password@localhost:5433/tasas_db")

# Crear engine
engine = create_engine(DATABASE_URL)

# Crear SessionLocal
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base para los modelos
Base = declarative_base()

# Dependency para obtener la sesión de DB
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
