from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Resort Social AI"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, ge=1, le=65535)
    api_v1_prefix: str = "/api/v1"
    web_base_url: str = "http://localhost:3000"
    public_api_base_url: str | None = None
    cors_origins: str = "http://localhost:3000"
    default_timezone: str = "Asia/Aden"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://resort:local-change-me@localhost:5432/resort_social"
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=100)

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    celery_worker_concurrency: int = Field(default=2, ge=1, le=64)

    token_encryption_key: SecretStr
    jwt_secret_key: SecretStr
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_ttl_minutes: int = Field(default=15, ge=5, le=60)
    invitation_ttl_hours: int = Field(default=72, ge=1, le=168)
    ai_auto_reply_enabled: bool = False
    ai_require_human_approval: bool = True
    ai_provider: Literal["openai", "anthropic", "gemini"] = "openai"
    ai_model: str = "gpt-4.1-mini"
    ai_embedding_model: str = "text-embedding-3-small"
    ai_confidence_threshold: float = Field(default=0.88, ge=0.0, le=1.0)
    ai_min_retrieval_similarity: float = Field(default=0.68, ge=0.0, le=1.0)
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    ai_max_reply_chars: int = Field(default=1800, ge=100, le=5000)

    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    meta_api_version: str = "v25.0"
    meta_oauth_state_ttl_minutes: int = Field(default=10, ge=2, le=30)
    meta_oauth_redirect_uri: str = "http://localhost:8000/api/v1/integrations/meta/callback"
    meta_webhook_verify_token: SecretStr | None = None
    meta_webhook_app_secret: SecretStr | None = None
    whatsapp_webhook_verify_token: SecretStr | None = None
    marketing_mock_publish: bool = False
    sentry_dsn: str | None = None

    @field_validator("api_v1_prefix")
    @classmethod
    def prefix_must_be_absolute_path(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("api_v1_prefix must start with '/' and must not end with '/'")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        origins = [item.strip() for item in value.split(",") if item.strip()]
        if not origins:
            raise ValueError("cors_origins must contain at least one origin")
        for origin in origins:
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"Invalid CORS origin: {origin}")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError(f"CORS origins must be origins only, not paths: {origin}")
        return ",".join(origins)

    @model_validator(mode="after")
    def production_safety_checks(self):
        jwt_secret = self.jwt_secret_key.get_secret_value()
        if len(jwt_secret.encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 bytes")
        if self.app_env == "production":
            if self.marketing_mock_publish:
                raise ValueError("MARKETING_MOCK_PUBLISH must be false in production")
            if self.app_debug:
                raise ValueError("APP_DEBUG must be false in production")
            if "localhost" in self.web_base_url or "localhost" in self.meta_oauth_redirect_uri:
                raise ValueError("Production URLs must not point to localhost")
            if self.ai_auto_reply_enabled and self.ai_require_human_approval:
                # Valid but contradictory for automatic sending; keep human approval as the safe mode.
                pass
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
