from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


def normalize_database_url(url: str) -> str:
    """Render/Heroku often give postgres://; SQLAlchemy + psycopg need postgresql+psycopg://."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    app_name: str = "Usyd Due"
    base_url: str = "http://127.0.0.1:8000"
    secret_key: str = "dev-change-me-please-use-long-random-string"
    # Fernet key: run `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
    token_fernet_key: str = ""
    database_url: str = f"sqlite:///{ROOT / 'data' / 'usyd_due.db'}"
    timezone: str = "Australia/Sydney"

    # Mail: leave empty to log magic links / digests to console (dev)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Usyd Due <noreply@localhost>"
    resend_api_key: str = ""
    # When true (set on Render), refuse to silently print mail — require Resend or SMTP.
    require_mail: bool = False

    session_hours: int = 24 * 14
    magic_link_minutes: int = 20

    default_horizon_days: int = 3
    morning_hour: int = 8
    evening_hour: int = 18

    demo_email: str = "demo@usyd-due.local"
    github_url: str = "https://github.com/OceanHu123/usyd-due-reminders"

    @property
    def cookie_secure(self) -> bool:
        return self.base_url.strip().lower().startswith("https://")

    @property
    def mail_configured(self) -> bool:
        return bool(self.resend_api_key.strip() or self.smtp_host.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


def database_url() -> str:
    return normalize_database_url(get_settings().database_url)
