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
    discover_canvas_courses,
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


def ensure_courses_from_canvas(
    db: Session,
    user: User,
    *,
    only_suggested: bool = True,
) -> dict:
    """Auto-discover the user's Canvas courses via their token and reconcile.

    Returns a status dict so callers can explain what happened:
      {added: [...], updated: [...], skipped: [...], orphan: [...], total: N}

    Rules:
      - Existing course rows: if code matches a discovered course but canvas_id
        differs (or was None), backfill the correct canvas_id.
      - Discovered courses with `suggested=True` that have no matching code row:
        INSERT as new UserCourse.
      - Discovered courses with `suggested=False` (portals/hubs): never added
        automatically, they go into `skipped` so the Settings UI can offer an
        "Add anyway" button.
      - Existing rows whose code is NOT present in discovered set are flagged
        `orphan` — kept (we never silently delete user data) but the UI may
        highlight them.
    """
    status = {
        "added": [],
        "updated": [],
        "skipped": [],
        "orphan": [],
        "total": 0,
    }
    if not user.canvas_token_enc:
        return status
    from web.crypto import decrypt_secret

    creds = CanvasCreds(
        token=decrypt_secret(user.canvas_token_enc),
        api_url=_resolved_canvas_url(user),
    )
    discovered = discover_canvas_courses(creds)
    status["total"] = len(discovered)
    by_code: dict[str, dict] = {d["code"]: d for d in discovered if d["code"]}
    seen_codes: set[str] = set()
    existing = list(db.query(UserCourse).filter(UserCourse.user_id == user.id).all())
    for ec in existing:
        seen_codes.add(ec.code.upper())
        match = by_code.get(ec.code.upper())
        if match is None:
            status["orphan"].append(ec.code)
            continue
        if ec.canvas_id != match["canvas_id"]:
            ec.canvas_id = match["canvas_id"]
            status["updated"].append(ec.code)
    for d in discovered:
        if not d["code"]:
            status["skipped"].append({"code": d["raw_code"] or d["name"], "reason": "no_code"})
            continue
        key = d["code"].upper()
        if key in seen_codes:
            continue
        if only_suggested and not d["suggested"]:
            status["skipped"].append(
                {"code": d["code"], "name": d["name"], "reason": "not_suggested"}
            )
            continue
        db.add(
            UserCourse(
                user_id=user.id,
                code=key,
                canvas_id=d["canvas_id"],
                ed_id=None,
            )
        )
        status["added"].append(key)
    db.commit()
    db.refresh(user)
    return status


def sync_user_dues(db: Session, user: User) -> list[DueCache]:
    ensure_default_courses(db, user)
    # Auto-discover + backfill canvas_ids from the user's live enrollment list.
    try:
        ensure_courses_from_canvas(db, user)
    except Exception:  # noqa: BLE001 — discovery best-effort; keep syncing
        pass
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
