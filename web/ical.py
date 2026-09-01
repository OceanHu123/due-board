"""Minimal iCalendar export: dues (deadlines) + extra tasks + recurring blocks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from web.db import DueCache, ExtraTask, RecurringTask, User

_UTC = timezone.utc


def _fmt_utc(dt: datetime) -> str:
    return dt.astimezone(_UTC).strftime("%Y%m%dT%H%M%SZ")


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _parse_extra_due(raw: str, tz: ZoneInfo) -> datetime | None:
    """ExtraTask.due_at is user-entered text; best-effort ISO parsing."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def _vevent(uid: str, start: datetime, end: datetime, summary: str, url: str, desc: str) -> list[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{_esc(uid)}",
        f"DTSTAMP:{_fmt_utc(datetime.now(_UTC))}",
        f"DTSTART:{_fmt_utc(start)}",
        f"DTEND:{_fmt_utc(end)}",
        f"SUMMARY:{_esc(summary)}",
    ]
    if desc:
        lines.append(f"DESCRIPTION:{_esc(desc)}")
    if url:
        lines.append(f"URL:{_esc(url)}")
    lines.append("END:VEVENT")
    return lines


def recurring_occurrences(
    tasks: list[RecurringTask], tz: ZoneInfo, *, days: int = 7, now: datetime | None = None
) -> list[tuple[RecurringTask, datetime, datetime]]:
    """Expand weekly blocks into concrete (task, start, end) within the next `days`."""
    now = now or datetime.now(tz)
    today = now.date()
    out: list[tuple[RecurringTask, datetime, datetime]] = []
    for t in tasks:
        for offset in range(days + 1):
            day = today + timedelta(days=offset)
            if day.weekday() != t.weekday:
                continue
            try:
                start = datetime.combine(day, datetime.strptime(t.start_hm, "%H:%M").time(), tzinfo=tz)
                end = datetime.combine(day, datetime.strptime(t.end_hm, "%H:%M").time(), tzinfo=tz)
            except ValueError:
                continue
            if end <= now:
                continue
            out.append((t, start, end))
    out.sort(key=lambda x: x[1])
    return out


def build_ics(db: Session, user: User) -> str:
    tz = ZoneInfo(user.timezone or "Australia/Sydney")
    now = datetime.now(tz)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//DueBoard//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc(f'DueBoard · {user.email}')}",
    ]
    for r in db.query(DueCache).filter(DueCache.user_id == user.id).all():
        due = r.due_at if r.due_at.tzinfo else r.due_at.replace(tzinfo=tz)
        lines += _vevent(
            f"due-{r.id}@dueboard",
            due,
            due + timedelta(minutes=30),
            f"{r.course}: {r.title}",
            r.url or "",
            r.detail or r.source or "",
        )
    for e in db.query(ExtraTask).filter(ExtraTask.user_id == user.id).all():
        start = _parse_extra_due(e.due_at, tz)
        if start is None:
            continue
        lines += _vevent(
            f"extra-{e.id}@dueboard",
            start,
            start + timedelta(minutes=30),
            f"{e.course}: {e.title}",
            e.url or "",
            "Extra task",
        )
    for task, start, end in recurring_occurrences(user.recurring_tasks, tz, days=28, now=now):
        lines += _vevent(
            f"recur-{task.id}-{start.strftime('%Y%m%d')}@dueboard",
            start,
            end,
            f"{task.course}: {task.title}",
            task.url or "",
            "Recurring time block",
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
