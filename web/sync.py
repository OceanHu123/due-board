from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from dues_lib import (
    DEFAULT_EXCLUDE,
    CanvasCreds,
    CourseRef,
    EdCreds,
    collect_dues,
    window_filter,
)
from web.config import (
    default_canvas_url_for,
    default_courses_for,
    default_ed_base_url_for,
)
from web.crypto import decrypt_secret
from web.db import DueCache, ExtraTask, User, UserCourse


def ensure_default_courses(db: Session, user: User) -> None:
    """Seed the user's course list from their institution's defaults.

    Only runs when the user has **zero** configured courses — users who
    manually edited their courses won't see repeated seeds.
    """
    # Bypass the ORM relationship cache — query directly so we don't get
    # a stale [] from a user object created before rows were INSERTed.
    count = (
        db.query(UserCourse).filter(UserCourse.user_id == user.id).count()
    )
    if count > 0:
        # Refresh the relationship so caller-side user.courses is populated.
        db.flush()
        db.refresh(user)
        return
    for row in default_courses_for(user.institution_code):
        db.add(
            UserCourse(
                user_id=user.id,
                code=row["code"],
                canvas_id=row.get("canvas_id"),
                ed_id=row.get("ed_id"),
            )
        )
    db.commit()
    db.refresh(user)


def _resolved_canvas_url(user: User) -> str:
    """Institution default, overridden by explicit per-user canvas_api_url if set."""
    if user.canvas_api_url and user.canvas_api_url.strip():
        return user.canvas_api_url.strip().rstrip("/")
    return default_canvas_url_for(user.institution_code)


def _resolved_ed_base_url(user: User) -> str:
    if user.ed_base_url and user.ed_base_url.strip():
        return user.ed_base_url.strip().rstrip("/")
    return default_ed_base_url_for(user.institution_code)


def sync_user_dues(db: Session, user: User) -> list[DueCache]:
    ensure_default_courses(db, user)
    tz = ZoneInfo(user.timezone or "Australia/Sydney")
    courses = [
        CourseRef(code=c.code, canvas_id=c.canvas_id, ed_id=c.ed_id) for c in user.courses
    ]
    extras = [
        {
            "course": e.course,
            "title": e.title,
            "due_at": e.due_at,
            "url": e.url or None,
        }
        for e in user.extras
    ]
    canvas = None
    ed = None
    try:
        if user.canvas_token_enc:
            canvas = CanvasCreds(
                token=decrypt_secret(user.canvas_token_enc),
                api_url=_resolved_canvas_url(user),
            )
        if user.ed_token_enc:
            ed = EdCreds(
                token=decrypt_secret(user.ed_token_enc),
                base_url=_resolved_ed_base_url(user),
            )
        items = collect_dues(
            canvas=canvas,
            ed=ed,
            courses=courses,
            tz=tz,
            exclude=DEFAULT_EXCLUDE,
            extras=extras,
        )
        now = datetime.now(tz)
        filtered = window_filter(items, now, user.horizon_days or 3, tonight=False)
        # Also keep tonight items that might be outside horizon? horizon covers today.
        db.query(DueCache).filter(DueCache.user_id == user.id).delete()
        rows: list[DueCache] = []
        for item in filtered:
            row = DueCache(
                user_id=user.id,
                course=item.course,
                title=item.title,
                due_at=item.due,
                source=item.source,
                url=item.url or "",
                detail=item.detail or "",
            )
            db.add(row)
            rows.append(row)
        user.last_sync_at = datetime.now(timezone.utc)
        user.last_sync_error = ""
        db.commit()
        return rows
    except Exception as exc:  # noqa: BLE001 — surface to UI
        user.last_sync_at = datetime.now(timezone.utc)
        user.last_sync_error = str(exc)[:1000]
        db.commit()
        raise
