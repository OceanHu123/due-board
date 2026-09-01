"""Shared Canvas + Ed due fetching and filtering.

Multi-institution compatible: CanvasCreds / EdCreds carry per-institution URLs
(defaults are intentionally left blank so callers can pass institution-specific
values, usually from web.config.institution_by_code).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import httpx

DEFAULT_EXCLUDE = ("Drill", "Drills", "EFT Session")

# NOTE: Legacy default courses preserved for backwards compatibility. Prefer
# `web.config.default_courses_for(code)` when you know the institution, which
# reads from institutions.yaml and per-institution default courses.
_DEFAULT_COURSES_LEGACY: list[dict[str, Any]] = [
    {"code": "INFO1112", "canvas_id": 73745, "ed_id": 36385},
    {"code": "INFO1113", "canvas_id": 73747, "ed_id": 36387},
    {"code": "MATH1064", "canvas_id": 74722, "ed_id": 37261},
    {"code": "ELEC1601", "canvas_id": 74259, "ed_id": 39516},
]


def default_courses(institution_code: str = "usyd") -> list[dict[str, Any]]:
    """Return default courses for an institution.

    If the institution registry (web.config) is available, that is the source of
    truth. Otherwise fall back to the legacy list (so dues_lib still works as a
    standalone library outside the web app, e.g. in remind.py).
    """
    try:
        from web.config import default_courses_for  # noqa: WPS433

        return default_courses_for(institution_code)
    except Exception:  # noqa: BLE001 — web.config missing means standalone usage
        if institution_code == "usyd":
            return list(_DEFAULT_COURSES_LEGACY)
        return []


@dataclass(frozen=True)
class CourseRef:
    code: str
    canvas_id: int | None = None
    ed_id: int | None = None


@dataclass(frozen=True)
class DueItem:
    course: str
    title: str
    due: datetime
    source: str
    url: str | None = None
    detail: str | None = None

    def line(self, now: datetime) -> str:
        when = self.due.strftime("%-d %b %-I:%M%p").replace("AM", "am").replace("PM", "pm")
        if self.due.date() == now.date():
            return f"{self.course} {self.title} (today {when})"
        return f"{self.course} {self.title} ({when})"

    def remaining(self, now: datetime) -> str:
        delta = self.due - now
        if delta.total_seconds() < 0:
            return "Overdue"
        hours = int(delta.total_seconds() // 3600)
        if hours < 24:
            mins = int((delta.total_seconds() % 3600) // 60)
            return f"{hours}h {mins}m left"
        days = hours // 24
        rem_h = hours % 24
        return f"{days}d {rem_h}h left"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["due"] = self.due.isoformat()
        return d


@dataclass(frozen=True)
class CanvasCreds:
    token: str
    # Intentionally no hardcoded default — callers pass the institution URL or a
    # user override. Passing an empty string triggers an explicit ValueError.
    api_url: str = ""


@dataclass(frozen=True)
class EdCreds:
    token: str
    # Intentionally no hardcoded default.
    base_url: str = ""


def parse_dt(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def paginate_canvas(client: httpx.Client, url: str, params: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    while url:
        r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            out.extend(data)
        url = r.links.get("next", {}).get("url")
        params = None
    return out


def _excluded(title: str, exclude: Sequence[str]) -> bool:
    low = title.lower()
    return any(s.lower() in low for s in exclude)


# Courses whose name/code contains these words are very unlikely to carry
# real graded dues (student portals, hubs, orientation, lab inventory…) and
# should be hidden by default during auto-import. Users can still add them
# manually from Settings if needed.
_PORTAL_HINTS = (
    "portal",
    "hub",
    "orientation",
    "network",
    "ec)",
    "_ec",
    " awareness",
    "dalyell",
    "labs",
    " scholars",
    "year 1",
)


def _looks_like_real_course(code: str, name: str) -> bool:
    """Heuristic: keep standard graded course codes and drop portals/hubs.

    Accepts codes like 'INFO1112 (ND)' / 'MATH1064' / 'INFO1111'.
    Rejects codes like 'ENGINEERING STUDENT PORTAL' / 'Women in Engineering'.
    """
    combined = f"{code or ''} {name or ''}".lower()
    if any(h in combined for h in _PORTAL_HINTS):
        return False
    # Normal graded course codes look like LETTERS-DIGITS, e.g. INFO1112.
    import re

    coarse_code = (code or name or "").strip().split()[0]
    return bool(re.match(r"^[A-Z]{3,6}\d{3,5}[A-Z]?$", coarse_code))


def _normalise_course_code(raw: str) -> str:
    """'ELEC1601 (ND)' → 'ELEC1601'; 'INFO1112' → 'INFO1112'."""
    if not raw:
        return ""
    head = raw.strip().split()[0]
    import re

    m = re.match(r"^([A-Za-z]{2,6})(\d{3,5})[A-Za-z]?$", head)
    if not m:
        return head.upper()
    return f"{m.group(1).upper()}{m.group(2)}"


def discover_canvas_courses(creds: CanvasCreds) -> list[dict[str, Any]]:
    """List the Canvas courses the token owner is actively enrolled in.

    Returns a list of dicts with keys:
        canvas_id  (int)        – LMS course ID (usable for assignments API)
        code       (str)        – normalised course code, e.g. 'INFO1112'
        raw_code   (str)        – code as Canvas returned it
        name       (str)        – human course name
        term       (str|None)   – term / enrollment term label if available
        role       (str|None)   – e.g. 'StudentEnrollment'
        suggested  (bool)       – True when we think it's a real graded course

    Raises ValueError on bad credentials / malformed URL.
    """
    token = creds.token.strip()
    base = creds.api_url.rstrip("/")
    if not token or not base:
        raise ValueError("Canvas token/api_url missing")
    headers = {"Authorization": f"Bearer {token}"}
    out: list[dict[str, Any]] = []
    with httpx.Client(timeout=30.0, headers=headers) as client:
        rows = paginate_canvas(
            client,
            f"{base}/courses",
            {
                "per_page": 100,
                "enrollment_type": "student",
                "include[]": ["term"],
            },
        )
        for c in rows:
            if not isinstance(c, dict):
                continue
            cid = c.get("id")
            if not isinstance(cid, int):
                continue
            raw_code = (c.get("course_code") or c.get("code") or "").strip()
            norm_code = _normalise_course_code(raw_code)
            name = (c.get("name") or c.get("original_name") or "").strip()
            term_obj = c.get("term") or {}
            term = term_obj.get("name") if isinstance(term_obj, dict) else None
            enrolls = c.get("enrollments") or []
            role = enrolls[0].get("type") if isinstance(enrolls, list) and enrolls else None
            suggested = _looks_like_real_course(raw_code or name, name)
            out.append(
                {
                    "canvas_id": cid,
                    "code": norm_code,
                    "raw_code": raw_code,
                    "name": name,
                    "term": term,
                    "role": role,
                    "suggested": suggested,
                }
            )
    # Stable order: suggested courses first, then alphabetic by code.
    out.sort(key=lambda x: (0 if x["suggested"] else 1, x["code"] or ""))
    return out


def canvas_items(
    creds: CanvasCreds,
    courses: Sequence[CourseRef],
    tz: ZoneInfo,
    exclude: Sequence[str] = DEFAULT_EXCLUDE,
) -> list[DueItem]:
    token = creds.token.strip()
    base = creds.api_url.rstrip("/")
    if not token or not base:
        raise ValueError("Canvas token/api_url missing")
    items: list[DueItem] = []
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=30.0, headers=headers) as client:
        for course in courses:
            if course.canvas_id is None:
                continue
            rows = paginate_canvas(
                client,
                f"{base}/courses/{course.canvas_id}/assignments",
                {"per_page": 100, "order_by": "due_at", "include[]": "submission"},
            )
            for a in rows:
                title = (a.get("name") or "").strip()
                if not title or _excluded(title, exclude):
                    continue
                types = a.get("submission_types") or []
                if types == ["none"] or (len(types) == 1 and types[0] == "none"):
                    continue
                due = parse_dt(a.get("due_at") or a.get("lock_at"), tz)
                if due is None:
                    continue
                sub = a.get("submission") or {}
                state = (sub.get("workflow_state") or "").lower()
                if state in {"submitted", "graded"}:
                    continue
                points = a.get("points_possible")
                detail_bits = ["Canvas"]
                if points is not None:
                    detail_bits.append(f"{points} pts")
                items.append(
                    DueItem(
                        course.code,
                        title,
                        due,
                        "canvas",
                        url=a.get("html_url"),
                        detail=" · ".join(detail_bits),
                    )
                )
    return items


def _lesson_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("lessons", "modules", "challenge_sets", "challenges"):
        val = payload.get(key)
        if isinstance(val, list):
            rows: list[dict[str, Any]] = []
            for item in val:
                if not isinstance(item, dict):
                    continue
                if item.get("title") or item.get("name") or item.get("due_at"):
                    rows.append(item)
                for nested_key in ("lessons", "slides", "challenges"):
                    nested = item.get(nested_key)
                    if isinstance(nested, list):
                        rows.extend(x for x in nested if isinstance(x, dict))
            return rows
    lesson = payload.get("lesson")
    if isinstance(lesson, dict):
        return [lesson]
    return []


def ed_items(
    creds: EdCreds,
    courses: Sequence[CourseRef],
    tz: ZoneInfo,
    exclude: Sequence[str] = DEFAULT_EXCLUDE,
) -> list[DueItem]:
    token = creds.token.strip()
    base = creds.base_url.rstrip("/")
    if not token:
        raise ValueError("Ed token missing")
    items: list[DueItem] = []
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=30.0, headers=headers) as client:
        for course in courses:
            if course.ed_id is None:
                continue
            payloads: list[Any] = []
            for path in (
                f"{base}/courses/{course.ed_id}/lessons",
                f"{base}/courses/{course.ed_id}/challenges",
            ):
                r = client.get(path)
                if r.status_code == 200:
                    payloads.append(r.json())
            for payload in payloads:
                for row in _lesson_rows(payload):
                    title = (row.get("title") or row.get("name") or "").strip()
                    if not title or _excluded(title, exclude):
                        continue
                    due = parse_dt(row.get("due_at") or row.get("deadline"), tz)
                    if due is None:
                        continue
                    if row.get("submitted_at"):
                        continue
                    lid = row.get("id")
                    url = (
                        f"https://edstem.org/au/courses/{course.ed_id}/lessons/{lid}"
                        if lid
                        else None
                    )
                    items.append(
                        DueItem(course.code, title, due, "ed", url=url, detail="Ed Lesson")
                    )
    return items


def extra_items(
    rows: Sequence[dict[str, Any]],
    tz: ZoneInfo,
) -> list[DueItem]:
    items: list[DueItem] = []
    for row in rows:
        due = parse_dt(str(row.get("due_at") or ""), tz)
        if due is None:
            continue
        items.append(
            DueItem(
                str(row["course"]),
                str(row["title"]),
                due,
                "extra",
                url=row.get("url"),
                detail=str(row.get("detail") or "extra task"),
            )
        )
    return items


def window_filter(
    items: Sequence[DueItem],
    now: datetime,
    horizon_days: int,
    tonight: bool = False,
) -> list[DueItem]:
    end = (now + timedelta(days=horizon_days)).replace(
        hour=23, minute=59, second=59, microsecond=999999
    )
    out: list[DueItem] = []
    seen: set[tuple[str, str, str]] = set()
    for item in sorted(items, key=lambda x: (x.due, x.course, x.title)):
        if item.due < now:
            continue
        if tonight:
            if item.due.date() != now.date():
                continue
        elif item.due > end:
            continue
        key = (item.course, item.title.lower(), item.due.isoformat())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def collect_dues(
    *,
    canvas: CanvasCreds | None,
    ed: EdCreds | None,
    courses: Sequence[CourseRef],
    tz: ZoneInfo,
    exclude: Sequence[str] = DEFAULT_EXCLUDE,
    extras: Sequence[dict[str, Any]] | None = None,
) -> list[DueItem]:
    items: list[DueItem] = []
    if canvas and canvas.token.strip():
        items.extend(canvas_items(canvas, courses, tz, exclude=exclude))
    if ed and ed.token.strip():
        items.extend(ed_items(ed, courses, tz, exclude=exclude))
    if extras:
        items.extend(extra_items(extras, tz))
    return items
