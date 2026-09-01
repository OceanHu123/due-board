from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import web.db as _db
from web.config import get_settings
from web.db import DueCache, User, init_db, reconfigure_db


def SessionLocal():  # noqa: N802
    return _db.SessionLocal()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import sys

    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("SECRET_KEY", "test-secret-for-ci")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    get_settings.cache_clear()
    reconfigure_db()
    init_db()
    for mod in list(sys.modules.keys()):
        if mod == "web.app" or mod.startswith("web.app."):
            del sys.modules[mod]
    from web.app import app  # noqa: WPS433
    init_db()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def _seed_due(client: TestClient, course="INFO1113", title="Test Task", days=1):
    """Insert a due directly into the DB for the singleton board user."""
    from web.auth import get_board_user

    db = SessionLocal()
    try:
        user = get_board_user(db)
        tz = ZoneInfo(user.timezone or "Australia/Sydney")
        due = datetime.now(tz) + timedelta(days=days)
        db.add(
            DueCache(
                user_id=user.id,
                course=course,
                title=title,
                due_at=due.astimezone(timezone.utc),
                source="test",
                url="",
                detail="",
            )
        )
        db.commit()
    finally:
        db.close()


def test_health(client: TestClient):
    h = client.get("/healthz")
    assert h.status_code == 200
    assert h.json()["ok"] is True


def test_board_loads(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "DueBoard" in r.text
    assert "What's left to do" in r.text


def test_settings_loads(client: TestClient):
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Institution" in r.text
    assert "Calendar feed" in r.text
    assert "/calendar/" in r.text


def test_course_filter(client: TestClient):
    _seed_due(client, course="INFO1113", title="Filter Me")
    _seed_due(client, course="MATH1064", title="Hide Me")
    r = client.get("/?course=INFO1113")
    assert "Filter Me" in r.text
    assert "Hide Me" not in r.text
    r2 = client.get("/?course=NOPE101")
    assert r2.status_code == 200


def test_htmx_fragment(client: TestClient):
    _seed_due(client, title="HX Item")
    r = client.get("/", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'id="board-region"' in r.text
    assert "HX Item" in r.text
    assert "<html" not in r.text.lower()


def test_htmx_boosted_nav_returns_full_page(client: TestClient):
    """Boosted link clicks (HX-Boosted=true) must get full HTML, not fragment."""
    r = client.get("/", headers={"HX-Request": "true", "HX-Boosted": "true"})
    assert r.status_code == 200
    assert "<html" in r.text.lower()
    assert "<header" in r.text.lower()


def test_refresh_htmx_and_fallback(client: TestClient):
    # HX POST → fragment (no tokens set, so sync will fail gracefully)
    rh = client.post("/refresh", headers={"HX-Request": "true"})
    assert rh.status_code == 200
    assert 'id="board-region"' in rh.text
    # Plain GET → 303 redirect
    rg = client.get("/refresh", follow_redirects=False)
    assert rg.status_code == 303


def test_extra_tasks(client: TestClient):
    due = (datetime.now(ZoneInfo("Australia/Sydney")) + timedelta(days=1)).isoformat()
    r = client.post(
        "/settings/extras",
        data={"course": "INFO1113", "title": "Extra Task Test", "due_at": due},
        follow_redirects=True,
    )
    assert "Extra Task Test" in r.text
    # Verify it's in the DB
    db = SessionLocal()
    try:
        assert db.query(_db.ExtraTask).filter(_db.ExtraTask.title == "Extra Task Test").count() == 1
    finally:
        db.close()


def test_calendar_ics(client: TestClient):
    s = client.get("/settings")
    ics_path = s.text.split("/calendar/")[1].split('"')[0]
    ics_path = "/calendar/" + ics_path
    _seed_due(client, title="ICS Due Item")
    ics = client.get(ics_path)
    assert ics.status_code == 200
    assert "text/calendar" in ics.headers["content-type"]
    assert "BEGIN:VCALENDAR" in ics.text
    assert "ICS Due Item" in ics.text
    assert client.get("/calendar/wrong-token.ics").status_code == 404


def test_recurring_time_blocks(client: TestClient):
    weekday = datetime.now().weekday()
    resp = client.post(
        "/settings/recurring",
        data={
            "course": "INFO1113",
            "title": "Weekly Lab",
            "weekday": str(weekday),
            "start_hm": "01:00",
            "end_hm": "02:00",
        },
        follow_redirects=True,
    )
    assert "Weekly Lab" in resp.text
    board = client.get("/").text
    assert "Recurring time blocks" in board
    assert "Weekly Lab" in board
    # Calendar includes it
    s = client.get("/settings")
    ics_path = "/calendar/" + s.text.split("/calendar/")[1].split('"')[0]
    assert "Weekly Lab" in client.get(ics_path).text
    # Delete
    db = SessionLocal()
    try:
        rec_id = db.query(_db.RecurringTask).filter(_db.RecurringTask.title == "Weekly Lab").one().id
    finally:
        db.close()
    resp2 = client.post(f"/settings/recurring/{rec_id}/delete", follow_redirects=True)
    assert "Weekly Lab" not in resp2.text.split("Recurring time blocks")[1]


def test_save_tokens(client: TestClient):
    r = client.post(
        "/settings/tokens",
        data={"canvas_token": "fake-token-123", "canvas_api_url": "", "ed_token": "", "ed_base_url": ""},
        follow_redirects=True,
    )
    assert "Saved" in r.text
    db = SessionLocal()
    try:
        user = db.query(User).first()
        assert user.canvas_token_enc  # encrypted token stored
    finally:
        db.close()


def test_institution_switch(client: TestClient):
    r = client.post(
        "/settings/institution",
        data={"institution_code": "unsw", "also_reset_courses": "1"},
        follow_redirects=True,
    )
    assert "Saved" in r.text
    db = SessionLocal()
    try:
        user = db.query(User).first()
        assert user.institution_code == "unsw"
    finally:
        db.close()
