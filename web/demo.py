"""Public demo account with seeded fictional dues (no real LMS tokens)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from web.config import get_settings
from web.db import DueCache, User
from web.sync import ensure_default_courses


def is_demo_user(user: User | None) -> bool:
    if user is None:
        return False
    return user.email.strip().lower() == get_settings().demo_email.strip().lower()


def seed_demo_dues(db: Session, user: User) -> None:
    """Replace demo due_cache with a few realistic-looking fictional items."""
    tz = ZoneInfo(user.timezone or "Australia/Sydney")
    now = datetime.now(tz)
    samples = [
        ("INFO1113", "Week Task (Challenge)", now + timedelta(hours=10), "ed", "Ed Lesson · demo"),
        ("INFO1112", "Week Quiz", now + timedelta(days=1, hours=14), "canvas", "Canvas · demo"),
        ("MATH1064", "Weekly Online Quiz", now + timedelta(days=2), "canvas", "Canvas · demo"),
        ("ELEC1601", "Lab prep checkpoint", now + timedelta(days=3, hours=2), "extra", "extra task · demo"),
    ]
    db.query(DueCache).filter(DueCache.user_id == user.id).delete()
    for course, title, due, source, detail in samples:
        db.add(
            DueCache(
                user_id=user.id,
                course=course,
                title=title,
                due_at=due.astimezone(timezone.utc),
                source=source,
                url="https://example.com/demo",
                detail=detail,
            )
        )
    user.canvas_token_enc = ""
    user.ed_token_enc = ""
    user.last_sync_at = datetime.now(timezone.utc)
    user.last_sync_error = ""
    user.email_reminders = False
    db.commit()


def ensure_demo_user(db: Session) -> User:
    settings = get_settings()
    email = settings.demo_email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, email_reminders=False)
        db.add(user)
        db.flush()
        ensure_default_courses(db, user)
    seed_demo_dues(db, user)
    db.refresh(user)
    return user
