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

    # Firebase & Storage
    FIREBASE_SERVICE_ACCOUNT_PATH: Optional[str] = None
    GCP_BUCKET_NAME: Optional[str] = None
    GCP_PROJECT_ID: Optional[str] = "edtech-107e3"

    # Razorpay
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""

    # App
    DEBUG: bool = True


    class Config:
        case_sensitive = False
        env_file = ".env"
        extra = "allow"


settings = Settings()
