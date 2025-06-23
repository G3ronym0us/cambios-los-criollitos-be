import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://tasas_user:tasas_password@localhost:5433/tasas_db")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    # Configuración de scraping
    SCRAPING_INTERVAL_MINUTES: int = 5
    SCRAPING_ENABLED: bool = True
    
    class Config:
        env_file = ".env"

settings = Settings()
