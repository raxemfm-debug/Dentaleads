from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str
    database_url_sync: str  # Used by Alembic (no async support in migrations)

    # Cache / broker
    redis_url: str

    # External APIs (required in production)
    anthropic_api_key: str
    whatsapp_verify_token: str
    whatsapp_app_secret: str
    whatsapp_access_token: str

    # Webhook
    webhook_max_message_age_seconds: int = 900

    # Auth
    jwt_secret: str

    # Runtime
    environment: Literal["development", "staging", "production"] = "development"

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v.startswith("postgresql"):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


settings = Settings()
