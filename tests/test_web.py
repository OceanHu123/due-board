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


def test_calendar_ics_feed(client: TestClient):
    # Real (non-demo) user: magic-link login flow.
    r = client.post("/login", data={"email": "cal@example.com"})
    m = re.search(r"token=([A-Za-z0-9_\-]+)", r.text)
    assert m
    client.get(f"/auth/verify?token={m.group(1)}")
    # Visiting settings lazily generates the subscription token.
    s = client.get("/settings")
    assert "/calendar/" in s.text
    ics_path = re.search(r'(/calendar/[A-Za-z0-9_\-]+\.ics)', s.text).group(1)
    # Add one extra task so the feed has content.
    client.post(
        "/settings/extras",
        data={"course": "INFO1113", "title": "Cal Export Task", "due_at": "2099-01-01T10:00:00+11:00"},
    )
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    due = (datetime.now(ZoneInfo("Australia/Sydney")) + timedelta(days=1)).isoformat()
    client.post(
        "/settings/extras",
        data={"course": "INFO1113", "title": "Cal Export Task", "due_at": due},
    )
    ics = client.get(ics_path)
    assert ics.status_code == 200
    assert "text/calendar" in ics.headers["content-type"]
    body = ics.text
    assert "BEGIN:VCALENDAR" in body
    assert "Cal Export Task" in body
    # Wrong token → 404.
    assert client.get("/calendar/not-a-real-token.ics").status_code == 404


def test_recurring_time_blocks(client: TestClient):
    from datetime import datetime

    r = client.post("/login", data={"email": "rec@example.com"})
    m = re.search(r"token=([A-Za-z0-9_\-]+)", r.text)
    assert m
    client.get(f"/auth/verify?token={m.group(1)}")
    # Weekly block on today's weekday → at least one occurrence within 7 days.
    weekday = datetime.now().weekday()
    resp = client.post(
        "/settings/recurring",
        data={
            "course": "INFO1113",
            "title": "Weekly Lab Block",
            "weekday": str(weekday),
            "start_hm": "01:00",
            "end_hm": "02:00",
        },
        follow_redirects=True,
    )
    assert "Weekly Lab Block" in resp.text
    # Board shows the recurring section.
    board = client.get("/").text
    assert "本周固定时间块" in board
    assert "Weekly Lab Block" in board
    # Calendar feed includes it too.
    s = client.get("/settings")
    ics_path = re.search(r'(/calendar/[A-Za-z0-9_\-]+\.ics)', s.text).group(1)
    assert "Weekly Lab Block" in client.get(ics_path).text
    # Delete it.
    db = SessionLocal()
    try:
        rec_id = (
            db.query(_db.RecurringTask).filter(_db.RecurringTask.title == "Weekly Lab Block").one().id
        )
    finally:
        db.close()
    resp2 = client.post(f"/settings/recurring/{rec_id}/delete", follow_redirects=True)
    assert "Weekly Lab Block" not in resp2.text.split("Recurring time blocks")[1]


def _login_user(client: TestClient, email: str) -> None:
    r = client.post("/login", data={"email": email})
    m = re.search(r"token=([A-Za-z0-9_\-]+)", r.text)
    assert m, r.text
    client.get(f"/auth/verify?token={m.group(1)}")


def test_lead_reminder_setting(client: TestClient):
    _login_user(client, "lead@example.com")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "lead@example.com").one()
    finally:
        db.close()
    r = client.post(
        "/settings/reminders",
        data={"reminder_lead_hours": "12", "email_reminders": "on"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    db = SessionLocal()
    try:
        user2 = db.query(User).filter(User.email == "lead@example.com").one()
        assert user2.reminder_lead_hours == 12
    finally:
        db.close()


def test_lead_reminder_once_per_day(client: TestClient, monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from dues_lib import DueItem
    from web import worker

    _login_user(client, "smart@example.com")
    db = SessionLocal()
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        worker, "send_email", lambda to, subject, text, html=None: sent.append((subject, text))
    )
    try:
        user = db.query(User).filter(User.email == "smart@example.com").one()
        user.reminder_lead_hours = 24
        user.last_lead_reminder_date = ""
        db.commit()
        now = datetime.now(ZoneInfo(user.timezone or "Australia/Sydney"))
        items = [
            DueItem(course="INFO1113", title="Due Soon Task", due=now + timedelta(hours=2), source="test"),
            DueItem(course="INFO1112", title="Outside Window", due=now + timedelta(hours=40), source="test"),
        ]
        n = worker.maybe_send_lead_reminder(db, user, now, items, user.email)
        assert n == 1
        assert len(sent) == 1
        assert "Due Soon Task" in sent[0][1]
        assert "Outside Window" not in sent[0][1]
        # Same day → deduped.
        n2 = worker.maybe_send_lead_reminder(db, user, now, items, user.email)
        assert n2 == 0
        assert len(sent) == 1
        # Lead window off → never sends.
        user.reminder_lead_hours = 0
        user.last_lead_reminder_date = ""
        db.commit()
        assert worker.maybe_send_lead_reminder(db, user, now, items, user.email) == 0
        assert len(sent) == 1
    finally:
        db.close()


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
