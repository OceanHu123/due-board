from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import web.db as _db
from web.config import get_settings
from web.db import DueCache, User, init_db, reconfigure_db
from web.demo import ensure_demo_user, is_demo_user


def SessionLocal():  # noqa: N802 — alias to always pick up the latest reconfigured engine
    return _db.SessionLocal()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import importlib, sys
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("SECRET_KEY", "test-secret-for-ci")
    monkeypatch.setenv("REQUIRE_MAIL", "false")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("SMTP_HOST", "")
    get_settings.cache_clear()
    reconfigure_db()
    init_db()
    # Re-import the FastAPI app fresh each test so startup hooks bind to the new engine.
    for mod in list(sys.modules.keys()):
        if mod == "web.app" or mod.startswith("web.app."):
            del sys.modules[mod]
    from web.app import app  # noqa: WPS433
    # Re-create tables on the current engine AFTER app is imported so startup handlers
    # run against the right database. (FastAPI startup does create_all, but do it here
    # explicitly so tables exist even before the context manager enter.)
    init_db()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_landing_and_health(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "Try demo" in r.text
    h = client.get("/healthz")
    assert h.status_code == 200
    assert h.json()["ok"] is True


def test_demo_login_seeds_dues(client: TestClient):
    r = client.post("/demo", follow_redirects=True)
    assert r.status_code == 200
    # Board copy is bilingual-friendly; accept either phrasing.
    assert "Demo mode" in r.text or "演示" in r.text
    assert "INFO1113" in r.text
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == get_settings().demo_email).one()
        assert is_demo_user(user)
        assert db.query(DueCache).filter(DueCache.user_id == user.id).count() >= 3
        assert not user.canvas_token_enc
    finally:
        db.close()


def test_board_course_filter(client: TestClient):
    client.post("/demo", follow_redirects=True)
    r = client.get("/?course=INFO1113")
    assert r.status_code == 200
    assert "INFO1113" in r.text
    # MATH1064 only appears as a filter chip label; its card title must be gone.
    assert "Weekly Online Quiz" not in r.text
    # Unknown course → filtered empty state.
    r2 = client.get("/?course=NOPE101")
    assert r2.status_code == 200
    assert "NOPE101" in r2.text  # mentioned in the empty-state message


def test_board_htmx_fragment(client: TestClient):
    client.post("/demo", follow_redirects=True)
    r = client.get("/", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'id="board-region"' in r.text
    assert "INFO1113" in r.text
    # Fragment responses must not contain the surrounding page shell.
    assert "<html" not in r.text.lower()


def test_refresh_htmx_fragment_and_fallback(client: TestClient):
    client.post("/demo", follow_redirects=True)
    # htmx POST → partial board region, no redirect.
    rh = client.post("/refresh", headers={"HX-Request": "true"})
    assert rh.status_code == 200
    assert 'id="board-region"' in rh.text
    assert "已刷新" in rh.text or "演示数据已刷新" in rh.text
    assert "<html" not in rh.text.lower()
    # Plain GET (no-JS fallback) → classic 303 redirect flow.
    rg = client.get("/refresh", follow_redirects=False)
    assert rg.status_code == 303
    assert rg.headers["location"].startswith("/?synced=")


def test_demo_settings_readonly(client: TestClient):
    client.post("/demo")
    r = client.post(
        "/settings/tokens",
        data={"canvas_token": "should-not-save", "ed_token": ""},
        follow_redirects=True,
    )
    assert "demo" in r.text.lower() or "read-only" in r.text.lower() or "Demo" in r.text
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == get_settings().demo_email).one()
        assert not user.canvas_token_enc
    finally:
        db.close()


def test_user_isolation(client: TestClient, monkeypatch):
    # User A via magic link (dev prints link)
    r = client.post("/login", data={"email": "alice@example.com"})
    m = re.search(r"token=([A-Za-z0-9_\-]+)", r.text)
    assert m
    client.get(f"/auth/verify?token={m.group(1)}")
    client.post(
        "/settings/extras",
        data={
            "course": "INFO1113",
            "title": "Alice Only Task",
            "due_at": "2099-01-01T10:00:00+11:00",
            "url": "",
        },
    )
    # widen horizon so far-future still... wait window is 3 days - use near due
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    due = (datetime.now(ZoneInfo("Australia/Sydney")) + timedelta(days=1)).isoformat()
    client.post(
        "/settings/extras",
        data={"course": "INFO1113", "title": "Alice Only Task", "due_at": due, "url": ""},
    )
    client.post("/sync")
    board_a = client.get("/").text
    assert "Alice Only Task" in board_a

    # User B
    client2 = TestClient(client.app)
    r = client2.post("/login", data={"email": "bob@example.com"})
    m = re.search(r"token=([A-Za-z0-9_\-]+)", r.text)
    assert m
    client2.get(f"/auth/verify?token={m.group(1)}")
    board_b = client2.get("/").text
    assert "Alice Only Task" not in board_b


def test_require_mail_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'm.db'}")
    monkeypatch.setenv("REQUIRE_MAIL", "true")
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("SECRET_KEY", "x")
    get_settings.cache_clear()
    from web.mailer import MailNotConfiguredError, send_email

    with pytest.raises(MailNotConfiguredError):
        send_email("a@b.com", "s", "body")
