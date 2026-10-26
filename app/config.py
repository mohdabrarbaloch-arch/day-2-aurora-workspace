"""Application settings, loaded from the environment.

Every value has a safe local default so `uvicorn app.main:app` works on a clean
checkout with no .env file at all.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_SECRET = "change-me-run-openssl-rand-hex-32"


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Core
    app_env: Literal["development", "production", "test"] = "development"
    app_name: str = "Aurora"
    app_base_url: str = "http://localhost:8000"

    # Secrets
    secret_key: str = _DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=720, ge=5, le=60 * 24 * 30)

    # Database
    database_url: str = "sqlite:///./aurora.db"
    sql_echo: bool = False

    # Uploads
    storage_dir: str = "./storage"
    max_upload_mb: int = Field(default=15, ge=1, le=100)
    max_image_edge: int = Field(default=1600, ge=256, le=6000)

    # CORS
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    # Rate limiting
    rate_limit_requests: int = Field(default=60, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    # AI provider
    ai_provider: Literal["local", "gemini"] = "local"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # Retrieval
    retrieval_top_k: int = Field(default=3, ge=1, le=10)
    retrieval_min_score: float = Field(default=0.35, ge=0.0, le=100.0)

    @field_validator("cors_origins")
    @classmethod
    def _strip_origins(cls, value: str) -> str:
        return value.strip()

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        """Parsed CORS allowlist. Wildcards are refused in production."""
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if self.is_production:
            origins = [o for o in origins if o != "*"]
        return origins or ["http://localhost:8000"]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def use_gemini(self) -> bool:
        """Gemini is only used when explicitly selected *and* a key is present."""
        return self.ai_provider == "gemini" and bool(self.gemini_api_key.strip())

    def resolve_secret(self) -> str:
        """Return the signing key, generating an ephemeral one for local dev.

        In production a real key is mandatory: silently signing tokens with a
        public default would let anyone forge a session.
        """
        if self.secret_key and self.secret_key != _DEFAULT_SECRET:
            return self.secret_key
        if self.is_production:
            raise RuntimeError(
                "SECRET_KEY is unset or still the default. "
                "Generate one with `openssl rand -hex 32` and set it in the environment."
            )
        return secrets.token_urlsafe(32)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
