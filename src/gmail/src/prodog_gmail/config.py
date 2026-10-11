from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    app_mode: str = "demo"
    app_base_url: str = "http://127.0.0.1:8765"
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    token_encryption_key: SecretStr
    database_path: Path = Path("data/prodog.db")
    session_ttl_seconds: int = Field(default=28800, ge=300, le=86400)

    @model_validator(mode="after")
    def validate_config(self):
        if self.app_mode not in {"demo", "google"}:
            raise ValueError("APP_MODE must be demo or google")
        url = urlsplit(self.app_base_url)
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "localhost"}
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("This local prototype requires a loopback http APP_BASE_URL")
        self.app_base_url = self.app_base_url.rstrip("/")
        try:
            Fernet(self.token_encryption_key.get_secret_value().encode("ascii"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Set TOKEN_ENCRYPTION_KEY using scripts/setup_local.py") from exc
        return self

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret.get_secret_value())

    @property
    def redirect_uri(self) -> str:
        return self.app_base_url + "/oauth/google/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
