from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK_JWT_SECRETS = {
    "change-me",
    "change-me-in-production",
    "changeme",
    "default",
    "replace-with-at-least-32-random-characters",
    "secret",
    "test-secret",
}


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/notes"
    jwt_secret: str
    jwt_expire_minutes: int = 60 * 24
    cors_origins: str = "http://localhost:5173"
    # Optional: empty/unset disables the telegram notification channel.
    telegram_bot_token: str | None = None
    # Reminder scheduler loop; tests turn it off (see tests/conftest.py).
    scheduler_enabled: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: str) -> str:
        secret = value.strip()
        if secret.lower() in _WEAK_JWT_SECRETS or len(secret) < 32:
            raise ValueError(
                "JWT_SECRET must be at least 32 characters and must not use a placeholder value"
            )
        return secret


settings = Settings()
