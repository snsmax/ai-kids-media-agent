from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    mongo_uri: SecretStr
    mongo_database: str = "children_media"
    redis_url: SecretStr
    operator_key: SecretStr
    reviewer_key: SecretStr
    telegram_token: SecretStr = SecretStr("")
    telegram_webhook_secret: SecretStr = SecretStr("")
    text_provider_url: str = ""
    image_provider_url: str = ""
    video_provider_url: str = ""
    voice_provider_url: str = ""
    provider_key: SecretStr = SecretStr("")
    lease_seconds: int = Field(default=600, ge=300, le=3600)
    max_attempts: int = Field(default=3, ge=1, le=10)

    def validate_security(self):
        operator = self.operator_key.get_secret_value()
        reviewer = self.reviewer_key.get_secret_value()
        if len(operator) < 32 or len(reviewer) < 32 or operator == reviewer:
            raise ValueError("Distinct operator/reviewer secrets of at least 32 characters are required")
        for url in (
            self.text_provider_url,
            self.image_provider_url,
            self.video_provider_url,
            self.voice_provider_url,
        ):
            if url and not url.startswith("https://"):
                raise ValueError("Provider endpoints must use HTTPS")


@lru_cache
def settings():
    value = Settings()
    value.validate_security()
    return value
