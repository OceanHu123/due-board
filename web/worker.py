from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from dues_lib import DueItem, window_filter
from web.config import get_settings
from web.db import DueCache, SessionLocal, User, init_db
from web.mailer import send_email
from web.sync import sync_user_dues

log = logging.getLogger("due_board.worker")


def _rows_to_items(rows: list[DueCache]) -> list[DueItem]:
    return [
        DueItem(
            course=r.course,
            title=r.title,
            due=r.due_at if r.due_at.tzinfo else r.due_at.replace(tzinfo=ZoneInfo("UTC")),
            source=r.source,
            url=r.url or None,
            detail=r.detail or None,
        )
        for r in rows
    ]


def _format_digest(items: list[DueItem], now: datetime, kind: str) -> tuple[str, str, str]:
    settings = get_settings()
    board = f"{settings.base_url.rstrip('/')}/"
    if kind == "evening":
        subject = f"{settings.app_name}: due tonight ({len(items)})"
        title = "Still due tonight"
    else:
        subject = f"{settings.app_name}: upcoming dues ({len(items)})"
        title = "Upcoming dues"
    lines = [item.line(now) for item in items]
    text = title + "\n\n" + "\n".join(f"- {ln}" for ln in lines)
    text += f"\n\nOpen board: {board}\n"
    lis_parts: list[str] = []
    for item in items:
        body = f"{item.course} — {item.title} — {item.remaining(now)}"
        if item.due.date() == now.date():
            lis_parts.append(f"<li><strong>{body}</strong></li>")
        else:
            lis_parts.append(f"<li>{body}</li>")
    html = (
        f"<h2>{title}</h2><ul>{''.join(lis_parts)}</ul>"
        f"<p><a href='{board}'>Open board</a></p>"
    )
    return subject, text, html


def process_user(
    db: Session,
    user: User,
    force_kind: str | None = None,
    *,
    to_override: str | None = None,
) -> None:
    from web.demo import is_demo_user

    if is_demo_user(user):
        return
    if not user.email_reminders and not force_kind:
        return
    tz = ZoneInfo(user.timezone or "Australia/Sydney")
    now = datetime.now(tz)
    try:
        sync_user_dues(db, user)
    except Exception as exc:  # noqa: BLE001
        log.exception("sync failed for %s: %s", user.email, exc)
        return

    db.refresh(user)
    rows = db.query(DueCache).filter(DueCache.user_id == user.id).all()
    items = _rows_to_items(rows)
    today = now.date().isoformat()
    morning_h = user.morning_hour if user.morning_hour is not None else 8
    evening_h = user.evening_hour if user.evening_hour is not None else 18
    to_addr = (to_override or user.email).strip().lower()

    kinds: list[str] = []
    if force_kind:
        kinds = [force_kind]
    else:
        # Run often (e.g. every 15m); send at most once per day per slot when hour matches.
        if now.hour == morning_h and user.last_morning_email_date != today:
            kinds.append("morning")
        if now.hour == evening_h and user.last_evening_email_date != today:
            kinds.append("evening")

    for kind in kinds:
        if kind == "evening":
            due = window_filter(items, now, user.horizon_days or 3, tonight=True)
            if not due:
                user.last_evening_email_date = today
                db.commit()
                continue
            subject, text, html = _format_digest(due, now, "evening")
            send_email(to_addr, subject, text, html)
            user.last_evening_email_date = today
        else:
            due = window_filter(items, now, user.horizon_days or 3, tonight=False)
            if not due:
                user.last_morning_email_date = today
                db.commit()
                continue
            subject, text, html = _format_digest(due, now, "morning")
            send_email(to_addr, subject, text, html)
            user.last_morning_email_date = today
        db.commit()
        log.info("sent %s email to %s (account %s, %s items)", kind, to_addr, user.email, len(due))


def run_once(
    *,
    force_email: str | None = None,
    only_email: str | None = None,
    to_override: str | None = None,
) -> None:
    """force_email: morning|evening for all (or filtered) users — for manual testing."""
    init_db()
    db = SessionLocal()
    try:
        q = db.query(User)
        if only_email:
            q = q.filter(User.email == only_email.strip().lower())
        users = q.all()
        if not users:
            log.warning("no users matched%s", f" email={only_email!r}" if only_email else "")
            return
        for user in users:
            process_user(db, user, force_kind=force_email, to_override=to_override)
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="DueBoard email worker")
    parser.add_argument("--force", choices=("morning", "evening"), default=None)
    parser.add_argument("--email", default=None, help="Only this account email (whose dues to sync)")
    parser.add_argument(
        "--to",
        default=None,
        help="Override recipient (e.g. Resend test inbox). Defaults to the account email.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run forever (for Render worker / long-running process)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=900,
        help="Seconds between loops when --loop is set (default 900)",
    )
    args = parser.parse_args(argv)
    if args.loop:
        while True:
            try:
                run_once(force_email=args.force, only_email=args.email, to_override=args.to)
            except Exception:  # noqa: BLE001
                log.exception("worker iteration failed")
            time.sleep(max(60, args.interval))
        return 0  # pragma: no cover
    run_once(force_email=args.force, only_email=args.email, to_override=args.to)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
