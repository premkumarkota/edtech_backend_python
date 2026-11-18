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

    # Razorpay — collection (student payments). Payments dashboard →
    # Account & Settings → API Keys (Quontriz account since 2026-10-30).
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""
    # Old account's webhook secret, accepted during the cutover window only so
    # late webhooks for orders created on the old account still verify.
    RAZORPAY_WEBHOOK_SECRET_PREVIOUS: str = ""

    # RazorpayX — payouts (teacher withdrawals). RazorpayX dashboard →
    # Settings → Developer Controls. Leave the key pair empty to reuse the PG keys
    # (same Razorpay account); set them if X issues its own pair.
    RAZORPAYX_KEY_ID: str = ""
    RAZORPAYX_KEY_SECRET: str = ""
    # RazorpayX → Settings → Developer Controls → Webhooks (separate from PG webhooks).
    RAZORPAYX_WEBHOOK_SECRET: str = ""
    # The RazorpayX business account number payouts are debited from
    # (RazorpayX → Account Statement / My Account; Current Account or the X Lite number).
    RAZORPAY_ACCOUNT_NUMBER: str = ""

    # Teacher payout rail: "razorpay" (RazorpayX, live from 2026-10-30) | "cashfree" (fallback)
    PAYOUT_PROVIDER: str = "razorpay"

    # Cashfree Payouts (teacher withdrawals) — Payouts Dashboard → Developers → API Keys.
    # These are PAYOUTS keys, not Payment Gateway keys.
    CASHFREE_PAYOUT_ENV: str = "test"            # test | prod
    CASHFREE_PAYOUT_CLIENT_ID: str = ""
    CASHFREE_PAYOUT_CLIENT_SECRET: str = ""      # also signs V2 webhooks
    # 2FA for prod without a static IP (Cloud Run): Developers → Two-Factor
    # Authentication → Public Key. Paste the full PEM (newlines or literal \n).
    CASHFREE_PAYOUT_PUBLIC_KEY: str = ""

    # Agora RTC
    AGORA_APP_ID: str = ""
    AGORA_APP_CERTIFICATE: str = ""

    # Anthropic (Claude) — powers AI quiz generation, the AI study planner,
    # and the AI chat bot.
    ANTHROPIC_API_KEY: str = ""              # paste your key here (in .env.dev): ANTHROPIC_API_KEY=sk-ant-...
    ANTHROPIC_MODEL: str = "claude-sonnet-5"

    # App — override with DEBUG=true in .env.dev for local development
    DEBUG: bool = False


    class Config:
        case_sensitive = False
        env_file = _env_file
        extra = "allow"


settings = Settings()
