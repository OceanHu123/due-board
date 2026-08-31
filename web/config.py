from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Multi-institution registry: load institutions.yaml once at import time.
# Do not crash on missing file (e.g. Docker build without the data dir) —
# fall back to the bare 'usyd' preset so the web app still boots.
# ---------------------------------------------------------------------------

_INSTITUTIONS_YAML = ROOT / "institutions.yaml"


def _load_institutions() -> list[dict[str, Any]]:
    if not _INSTITUTIONS_YAML.is_file():
        return [
            {
                "code": "usyd",
                "name": "University of Sydney",
                "canvas_url": "https://canvas.sydney.edu.au/api/v1",
                "ed_region": "au",
                "ed_base_url": "https://edstem.org/api",
                "default_courses": [],
            }
        ]
    data = yaml.safe_load(_INSTITUTIONS_YAML.read_text(encoding="utf-8")) or {}
    rows = data.get("institutions") or []
    for r in rows:
        r.setdefault("canvas_url", "")
        r.setdefault("ed_region", "au")
        r.setdefault("ed_base_url", "https://edstem.org/api")
        r.setdefault("default_courses", [])
    return rows


INSTITUTIONS: list[dict[str, Any]] = _load_institutions()
INSTITUTION_CODES: tuple[str, ...] = tuple(i["code"] for i in INSTITUTIONS)
DEFAULT_INSTITUTION_CODE: str = INSTITUTION_CODES[0] if INSTITUTION_CODES else "usyd"


def institutions_choices() -> list[tuple[str, str]]:
    """Returns [(code, display_name), ...] for HTML <select> dropdowns."""
    return [(i["code"], i["name"]) for i in INSTITUTIONS]


def institution_by_code(code: str) -> dict[str, Any] | None:
    return next((i for i in INSTITUTIONS if i["code"] == code), None)


def default_courses_for(code: str) -> list[dict[str, Any]]:
    inst = institution_by_code(code) or institution_by_code(DEFAULT_INSTITUTION_CODE)
    return list((inst or {}).get("default_courses") or [])  # type: ignore[arg-type]


def default_canvas_url_for(code: str) -> str:
    inst = institution_by_code(code) or {}
    return inst.get("canvas_url") or "https://canvas.sydney.edu.au/api/v1"


def default_ed_base_url_for(code: str) -> str:
    inst = institution_by_code(code) or {}
    return inst.get("ed_base_url") or "https://edstem.org/api"


# ---------------------------------------------------------------------------
# App settings
# ---------------------------------------------------------------------------


def normalize_database_url(url: str) -> str:
    """Render/Heroku often give postgres://; SQLAlchemy + psycopg need postgresql+psycopg://."""
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    # Render managed Postgres requires TLS.
    if ("render.com" in url or "dpg-" in url) and "sslmode=" not in url:
        url = url + ("&" if "?" in url else "?") + "sslmode=require"
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    app_name: str = "DueBoard"
    base_url: str = "http://127.0.0.1:8000"
    secret_key: str = "dev-change-me-please-use-long-random-string"
    # Fernet key: run `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
    token_fernet_key: str = ""
    database_url: str = f"sqlite:///{ROOT / 'data' / 'due_board.db'}"
    timezone: str = "Australia/Sydney"
    # Default institution for brand-new accounts before they pick one.
    default_institution_code: str = DEFAULT_INSTITUTION_CODE

    # Mail: leave empty to log magic links / digests to console (dev)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "DueBoard <noreply@localhost>"
    resend_api_key: str = ""
    # When true (set on Render), refuse to silently print mail — require Resend or SMTP.
    require_mail: bool = False

    session_hours: int = 24 * 14
    magic_link_minutes: int = 20

    default_horizon_days: int = 3
    morning_hour: int = 8
    evening_hour: int = 18

    demo_email: str = "demo@due-board.local"
    github_url: str = "https://github.com/OceanHu123/due-board"

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
