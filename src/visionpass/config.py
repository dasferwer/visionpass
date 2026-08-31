from functools import lru_cache

from pydantic import EmailStr, SecretStr
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
