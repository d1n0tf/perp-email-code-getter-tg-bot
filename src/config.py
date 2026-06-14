import json
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="allow",
    )

    tg_token: str | None = None
    tg_admins: list[int] = []
    imap_host: str = "outlook.office365.com"
    search_from: str = "team@mail.perplexity.ai"
    mail_folders: list[str] = ["INBOX", "Junk", "Junk Email"]
    email_store_path: Path = Path("email.json")
    taken_email_store_path: Path = Path("email_taken.json")
    subscription_key_store_path: Path = Path("keys.json")
    activated_key_store_path: Path = Path("activated_keys.json")
    legacy_user_store_path: Path = Path("legacy_users.json")
    user_locale_store_path: Path = Path("user_locales.json")
    concurrent_mail_workers: int = 20
    mail_wait_timeout_seconds: int = 600
    mail_recent_window_seconds: int = 300
    mail_poll_interval_min_seconds: float = 7.0
    mail_poll_interval_max_seconds: float = 10.0
    mail_reconnect_delay_seconds: float = 10.0
    mail_global_rate_limit_per_second: float = 2.0
    mail_global_backoff_base_seconds: float = 5.0
    mail_global_backoff_max_seconds: float = 120.0
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    web_base_path: str = "/perp-code-getter"
    web_admin_password: str | None = None

    @field_validator("tg_admins", mode="before")
    @classmethod
    def parse_admins(cls, value: Any) -> list[int]:
        if value in (None, "", []):
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return [int(item) for item in json.loads(stripped)]
            return [int(item.strip()) for item in stripped.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [int(item) for item in value]
        raise TypeError("tg_admins must be a list of integers or a comma-separated string")

    @field_validator("mail_folders", mode="before")
    @classmethod
    def parse_mail_folders(cls, value: Any) -> list[str]:
        if value in (None, "", []):
            return ["INBOX", "Junk", "Junk Email"]
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return [str(item) for item in json.loads(stripped)]
            return [item.strip() for item in stripped.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value]
        raise TypeError("mail_folders must be a list of strings or a comma-separated string")


settings = Settings()  # type: ignore[call-arg]


__all__ = ["settings"]
