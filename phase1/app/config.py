from __future__ import annotations
from typing import Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "dev-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AICHIP_", env_file=".env")
    app_name: str = "AI Chip Studio - Phase 1"
    environment: str = "development"  # "development" | "production"
    database_url: str = "sqlite:///./ai_chip_studio.db"
    jwt_secret_key: str = _DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    ai_provider: str = "gemini"
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.0-flash"
    anthropic_api_key: Optional[str] = None
    ai_model: str = "claude-sonnet-4-6"
    redis_url: str = "redis://redis:6379/0"
    celery_task_always_eager: bool = False
    storage_backend: str = "local"
    storage_root: str = "./storage"
    s3_bucket: str = "ai-chip-studio"
    s3_endpoint_url: Optional[str] = None
    s3_region: str = "us-east-1"
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    auto_create_tables: bool = True
    jobs_root: str = "./jobs"
    max_zip_size_bytes: int = 52428800
    max_zip_file_count: int = 500
    max_extracted_size_bytes: int = 524288000
    subprocess_timeout_seconds: int = 300
    usage_tiers: dict = {
        # jobs_per_month is the simple cap (any job type counts once,
        # tracked via UsageKind.job) -- the free-trial limit the business
        # decided on is 5 jobs/month. sim_minutes_per_month/ai_calls_per_month
        # remain as finer-grained secondary caps within that -- a free user
        # is blocked by whichever limit they hit first.
        "free": {"jobs_per_month": 5, "sim_minutes_per_month": 30, "ai_calls_per_month": 10},
        "pro": {"jobs_per_month": 200, "sim_minutes_per_month": 300, "ai_calls_per_month": 200},
        "team": {"jobs_per_month": -1, "sim_minutes_per_month": -1, "ai_calls_per_month": -1},
        "enterprise": {"jobs_per_month": -1, "sim_minutes_per_month": -1, "ai_calls_per_month": -1},
    }
    # Rate limiting (slowapi / Retry-After semantics, see app/main.py)
    rate_limit_default: str = "60/minute"
    rate_limit_login: str = "5/minute"
    # Comma-separated list of origins allowed to call this API from a
    # browser (e.g. "https://aichipstudio.netlify.app,https://aichipstudio.com").
    # Defaults to "*" (allow any origin) for free-tier convenience -- you
    # don't know your final Render/Netlify URL until after the first
    # deploy, so locking this down up front just means re-deploying once
    # you do know it. Tighten this once the real frontend URL is known;
    # "*" plus credentials=True is rejected by browsers anyway (see
    # app/main.py's CORSMiddleware setup), so this is safe by construction
    # as long as the API doesn't rely on cookies for auth (it doesn't --
    # JWT bearer tokens only).
    cors_allowed_origins: str = "*"

    @model_validator(mode="after")
    def _refuse_insecure_secret_in_production(self) -> "Settings":
        if self.environment == "production" and self.jwt_secret_key == _DEV_JWT_SECRET:
            raise RuntimeError(
                "AICHIP_ENVIRONMENT=production but AICHIP_JWT_SECRET_KEY is still the "
                "default dev value. Set a real secret (e.g. `openssl rand -hex 32`) "
                "before starting in production -- refusing to boot with a publicly "
                "known signing key."
            )
        return self


settings = Settings()
