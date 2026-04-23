import os
from pydantic_settings import BaseSettings
from typing import Optional

# Load .env.dev locally; in production (Cloud Run) env vars are injected directly
_env_file = ".env.dev" if os.path.exists(".env.dev") else None


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

    # Agora RTC
    AGORA_APP_ID: str = ""
    AGORA_APP_CERTIFICATE: str = ""

    # App
    DEBUG: bool = True


    class Config:
        case_sensitive = False
        env_file = _env_file
        extra = "allow"


settings = Settings()
