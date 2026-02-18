from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Load settings from .env file"""
    
    # Database
    DATABASE_URL: str
    
    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Firebase
    FIREBASE_SERVICE_ACCOUNT_PATH: Optional[str] = None
    
    # App
    DEBUG: bool = True
    
    class Config:
        env_file = ".env"


settings = Settings()
