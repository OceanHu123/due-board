from __future__ import annotations

import pytest

from web.config import normalize_database_url
from web.crypto import decrypt_secret, encrypt_secret


def test_fernet_roundtrip(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "unit-test-secret-key")
    from web.config import get_settings

    get_settings.cache_clear()
    token = "canvas-secret-token-xyz"
    enc = encrypt_secret(token)
    assert enc
    assert "canvas-secret" not in enc
    assert decrypt_secret(enc) == token


def test_normalize_database_url():
    assert normalize_database_url("postgres://u:p@h/db").startswith("postgresql+psycopg://")
    assert normalize_database_url("postgresql://u:p@h/db").startswith("postgresql+psycopg://")
    assert normalize_database_url("sqlite:///./x.db") == "sqlite:///./x.db"
