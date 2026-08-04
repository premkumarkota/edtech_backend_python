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
    FIREBASE_SERVICE_ACCOUNT_JSON: Optional[str] = None  # raw JSON string (for Cloud Run)
    GCP_BUCKET_NAME: Optional[str] = None
    GCP_PROJECT_ID: Optional[str] = "edtech-107e3"

    # Razorpay — collection (student payments)
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""

    # Razorpay X — payouts (teacher withdrawals)
    # The Current Account number linked to your Razorpay X account.
    # Find it in Razorpay Dashboard → Payouts → Settings → Account Number.
    RAZORPAY_ACCOUNT_NUMBER: str = ""

    # Agora RTC
    AGORA_APP_ID: str = ""
    AGORA_APP_CERTIFICATE: str = ""

    # AI / LLM (Azure OpenAI or OpenAI-compatible)
    LLM_BASE_URL: str = ""       # e.g. "https://your-resource.openai.azure.com" or "https://api.openai.com/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"  # Azure deployment name or OpenAI model ID
    LLM_API_VERSION: str = "2024-08-01-preview"  # Azure-only, ignored for OpenAI

    # Anthropic (Claude) — AI quiz generation
    ANTHROPIC_API_KEY: str = ""              # paste your key here (in .env.dev): ANTHROPIC_API_KEY=sk-ant-...
    ANTHROPIC_MODEL: str = "claude-sonnet-5"

    # App — override with DEBUG=true in .env.dev for local development
    DEBUG: bool = False


    class Config:
        case_sensitive = False
        env_file = _env_file
        extra = "allow"


settings = Settings()
