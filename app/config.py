from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Load settings from .env file"""

    # Environment: local | dev | prod
    APP_ENV: str = "local"

    # Database
    DATABASE_URL: str = "sqlite:///./test.db"  # Default to avoid crash

    # JWT
    SECRET_KEY: str = "default_unsafe_secret"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Firebase
    FIREBASE_SERVICE_ACCOUNT_PATH: Optional[str] = None

    # App
    DEBUG: bool = True

    class Config:
        env_file = ".env"


settings = Settings()
