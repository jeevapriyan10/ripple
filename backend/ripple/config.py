from functools import lru_cache
from typing import Literal, Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration settings.

    Centralized single source of truth reading from environment variables
    with sensible defaults.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = "ripple"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    CORS_ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:80",
    ]
    ENABLED_NETWORKS: str = "all"
    CARD_PAN_TOKEN_KEY: str | None = None
    ENABLE_ADAPTER_DRY_RUN: bool | None = None

    @model_validator(mode="after")
    def _resolve_dry_run_default(self) -> Self:
        """Default ENABLE_ADAPTER_DRY_RUN to True in development, False otherwise."""
        if self.ENABLE_ADAPTER_DRY_RUN is None:
            object.__setattr__(
                self,
                "ENABLE_ADAPTER_DRY_RUN",
                self.APP_ENV == "development",
            )
        return self

    @field_validator("CORS_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_cors_allowed_origins(cls, value: object) -> list[str]:
        """Parse comma-separated string or list into a list of allowed origin strings."""
        if isinstance(value, str):
            if not value.strip():
                return []
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        # pyrefly: ignore [implicit-any-type-argument]
        if isinstance(value, list | tuple | set):
            return [str(origin).strip() for origin in value if str(origin).strip()]
        return []


@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of the application settings."""
    return Settings()
