"""Validated application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


Environment = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    """Configuration loaded exclusively from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="BLOGOPS_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "BlogOps AI API"
    app_version: str = "0.1.0"
    environment: Environment = "local"
    debug: bool = False

    api_host: str = "0.0.0.0"  # noqa: S104 - container bind address
    api_port: int = 8000
    docs_enabled: bool = True
    metrics_enabled: bool = True
    metrics_auth_token: SecretStr | None = None
    trust_proxy_headers: bool = False

    database_url: str = "postgresql+asyncpg://blogops:blogops@postgres:5432/blogops"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout_seconds: float = 10.0
    database_statement_timeout_ms: int = 15_000

    redis_url: str = "redis://redis:6379/0"
    redis_health_timeout_seconds: float = 1.0
    dependency_health_timeout_seconds: float = 2.0

    s3_endpoint_url: str = "http://object-storage:9000"
    s3_region: str = "us-east-1"
    s3_bucket: str = "blogops"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    knowledge_max_upload_bytes: int = 25 * 1024 * 1024
    knowledge_presign_ttl_seconds: int = 900
    knowledge_fetch_max_bytes: int = 10 * 1024 * 1024
    knowledge_fetch_timeout_seconds: float = 15.0
    knowledge_fetch_max_redirects: int = 5
    clamav_host: str = "clamav"
    clamav_port: int = 3310
    clamav_timeout_seconds: float = 20.0

    secret_key: SecretStr = SecretStr("local-development-key-change-me")
    allowed_hosts: str = "localhost,127.0.0.1"
    cors_origins: str = ""

    log_level: str = "INFO"
    otel_enabled: bool = False
    otel_service_name: str = "blogops-api"
    otel_exporter_otlp_endpoint: str | None = None

    idempotency_ttl_seconds: int = 86_400
    idempotency_lock_seconds: int = 120
    outbox_batch_size: int = 100
    payment_webhook_max_bytes: int = 1024 * 1024
    security_webhook_max_bytes: int = 1024 * 1024

    @field_validator("database_url")
    @classmethod
    def validate_database_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("BLOGOPS_DATABASE_URL must use postgresql+asyncpg")
        return value

    @field_validator("api_port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if not 1 <= value <= 65_535:
            raise ValueError("BLOGOPS_API_PORT must be between 1 and 65535")
        return value

    @field_validator("payment_webhook_max_bytes")
    @classmethod
    def validate_payment_webhook_max_bytes(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("BLOGOPS_PAYMENT_WEBHOOK_MAX_BYTES must be positive")
        return value

    @field_validator("security_webhook_max_bytes")
    @classmethod
    def validate_security_webhook_max_bytes(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("BLOGOPS_SECURITY_WEBHOOK_MAX_BYTES must be positive")
        return value

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.environment in {"staging", "production"}:
            secret = self.secret_key.get_secret_value()
            if secret == "local-development-key-change-me" or len(secret) < 32:
                raise ValueError("BLOGOPS_SECRET_KEY must be a unique value of at least 32 characters")
            if self.debug:
                raise ValueError("BLOGOPS_DEBUG cannot be enabled outside local/test")
            if self.metrics_enabled and (
                self.metrics_auth_token is None
                or len(self.metrics_auth_token.get_secret_value()) < 32
            ):
                raise ValueError(
                    "BLOGOPS_METRICS_AUTH_TOKEN must be at least 32 characters when metrics are enabled"
                )
        return self

    @property
    def allowed_host_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide immutable configuration snapshot."""
    return Settings()
