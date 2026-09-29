from functools import lru_cache

from pydantic import EmailStr, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="VISIONPASS_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "VisionPass API"
    app_version: str = "0.1.0"
    database_url: str
    rabbitmq_url: str
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    admin_email: EmailStr = "admin@example.com"
    admin_password: SecretStr = SecretStr("ChangeMe123!")
    reviewer_email: EmailStr = "reviewer@example.com"
    reviewer_password: SecretStr = SecretStr("ChangeMe123!")
    matcher_backend: str = "stub"
    match_threshold: float = 0.60
    review_margin: float = 0.12
    max_image_bytes: int = 5 * 1024 * 1024
    log_level: str = "INFO"
    encryption_key: SecretStr
    image_retention_seconds: int = Field(default=900, ge=30, le=86400)
    template_retention_days: int = Field(default=1, ge=0, le=30)
    enrollment_lease_seconds: int = Field(default=120, ge=10, le=600)
    max_image_pixels: int = Field(default=8_000_000, ge=1000, le=20_000_000)
    max_match_candidates: int = Field(default=5000, ge=1, le=10000)

    @field_validator("encryption_key")
    @classmethod
    def valid_encryption_key(cls, value: SecretStr) -> SecretStr:
        import base64

        try:
            raw = base64.b64decode(value.get_secret_value(), validate=True)
        except ValueError as exc:
            raise ValueError("Нужен ключ AES-256 в Base64") from exc
        if len(raw) != 32:
            raise ValueError("Ключ AES-256 должен содержать 32 байта")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
