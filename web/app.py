from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from dues_lib import DueItem
from web.auth import create_session, current_user, logout, request_magic_link, verify_magic_link
from web.config import (
    DEFAULT_INSTITUTION_CODE,
    get_settings,
    institution_by_code,
    institutions_choices,
)
from web.crypto import encrypt_secret
from web.db import DueCache, ExtraTask, RecurringTask, SessionLocal, User, UserCourse, init_db
from web.demo import ensure_demo_user, is_demo_user, seed_demo_dues
from web.ical import build_ics, recurring_occurrences
from web.sync import ensure_default_courses, sync_user_dues

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="DueBoard")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _board_items(db: Session, user: User) -> list[DueItem]:
    rows = (
        db.query(DueCache)
        .filter(DueCache.user_id == user.id)
        .order_by(DueCache.due_at.asc())
        .all()
    )
    tz = ZoneInfo(user.timezone or "Australia/Sydney")
    items: list[DueItem] = []
    for r in rows:
        due = r.due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=tz)
        items.append(
            DueItem(
                course=r.course,
                title=r.title,
                due=due,
                source=r.source,
                url=r.url or None,
                detail=r.detail or None,
            )
        )
    return items


@app.get("/healthz")
def healthz():
    from web import db as dbmod

    try:
        with dbmod.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"db unavailable: {exc}") from exc


def _sync_stale(user: User, *, max_age_seconds: int = 300) -> bool:
    """True if board should re-pull Canvas/Ed (never synced or older than max_age)."""
    if is_demo_user(user):
        return False
    if not (user.canvas_token_enc or user.ed_token_enc):
        return False
    if user.last_sync_at is None:
        return True
    last = user.last_sync_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last).total_seconds()
    return age >= max_age_seconds


def _is_htmx(request: Request) -> bool:
    """True when the request comes from htmx (fragment responses swap into the page)."""
    return request.headers.get("HX-Request") == "true"


def _board_context(
    db: Session,
    user: User,
    request: Request,
    *,
    flash_synced: str | None = None,
    flash_error: str | None = None,
    auto_error: str | None = None,
) -> dict:
    """Shared context for the board page and its htmx fragments."""
    tz = ZoneInfo(user.timezone or "Australia/Sydney")
    now = datetime.now(tz)
    all_items = _board_items(db, user)
    selected = (request.query_params.get("course") or "").strip().upper()
    items = [i for i in all_items if i.course.upper() == selected] if selected else all_items
    courses_present: list[str] = []
    for i in all_items:
        if i.course not in courses_present:
            courses_present.append(i.course)
    # Recurring time blocks for the next 7 days, shown under the dues list.
    tz2 = ZoneInfo(user.timezone or "Australia/Sydney")
    blocks = [
        {"task": t, "start": s, "end": e}
        for t, s, e in recurring_occurrences(user.recurring_tasks, tz2, days=7, now=now)
    ]
    return {
        "user": user,
        "items": items,
        "now": now,
        "settings": get_settings(),
        "is_demo": is_demo_user(user),
        "flash_synced": flash_synced,
        "flash_error": flash_error,
        "auto_error": auto_error,
        "selected_course": selected or None,
        "courses_present": courses_present,
        "recurring_blocks": blocks,
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = current_user(db, request)
    if not user:
        return templates.TemplateResponse(
            request,
            "landing.html",
            {"user": None, "settings": get_settings()},
        )
    ensure_default_courses(db, user)
    # Opening the board refreshes remaining unfinished dues when cache is stale.
    flash_synced: str | None = None
    auto_error: str | None = None
    synced = request.query_params.get("synced")
    if synced is None and _sync_stale(user):
        try:
            if is_demo_user(user):
                seed_demo_dues(db, user)
            else:
                sync_user_dues(db, user)
            flash_synced = "已自动刷新未完成项。"
            db.refresh(user)
        except Exception as exc:  # noqa: BLE001
            auto_error = str(exc)[:120]
            db.refresh(user)
    elif synced == "demo":
        flash_synced = "演示数据已刷新。"
    elif synced:
        flash_synced = "已刷新：下面只显示还没交完的 due。"
    ctx = _board_context(
        db, user, request,
        flash_synced=flash_synced,
        flash_error=request.query_params.get("error"),
        auto_error=auto_error,
    )
    if _is_htmx(request):
        return templates.TemplateResponse(request, "partials/board_items.html", ctx)
    return templates.TemplateResponse(request, "board.html", ctx)


@app.api_route("/refresh", methods=["GET", "POST"])
def refresh_board(request: Request, db: Session = Depends(get_db)):
    """One-click refresh: re-sync then show remaining unfinished dues.

    htmx requests get the updated board region back directly (no page reload);
    plain browser requests keep the classic redirect flow.
    """
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if is_demo_user(user):
        seed_demo_dues(db, user)
        if _is_htmx(request):
            ctx = _board_context(db, user, request, flash_synced="演示数据已刷新。")
            return templates.TemplateResponse(request, "partials/board_items.html", ctx)
        return RedirectResponse("/?synced=demo", status_code=303)
    try:
        sync_user_dues(db, user)
    except Exception as exc:  # noqa: BLE001
        if _is_htmx(request):
            ctx = _board_context(db, user, request, flash_error=str(exc)[:120])
            return templates.TemplateResponse(request, "partials/board_items.html", ctx)
        return RedirectResponse(f"/?error={quote(str(exc)[:120])}", status_code=303)
    if _is_htmx(request):
        ctx = _board_context(db, user, request, flash_synced="已刷新：下面只显示还没交完的 due。")
        return templates.TemplateResponse(request, "partials/board_items.html", ctx)
    return RedirectResponse("/?synced=1", status_code=303)


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "privacy.html",
        {"user": current_user(db, request), "settings": get_settings()},
    )


@app.post("/demo")
def try_demo(request: Request, db: Session = Depends(get_db)):
    user = ensure_demo_user(db)
    response = RedirectResponse("/", status_code=303)
    create_session(db, user, response)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db), sent: str | None = None):
    if current_user(db, request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "user": None,
            "sent": sent,
            "dev_link": None,
            "settings": get_settings(),
        },
    )


@app.post("/login")
def login_submit(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    try:
        dev_link = request_magic_link(db, email)
    except HTTPException as exc:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "user": None,
                "sent": None,
                "error": exc.detail,
                "dev_link": None,
                "settings": get_settings(),
            },
            status_code=400,
        )
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "user": None,
            "sent": email.strip().lower(),
            "dev_link": dev_link or None,
            "settings": get_settings(),
        },
    )


@app.get("/auth/verify")
def auth_verify(token: str, db: Session = Depends(get_db)):
    response = RedirectResponse("/", status_code=303)
    verify_magic_link(db, token, response)
    return response


@app.post("/logout")
def do_logout(request: Request, db: Session = Depends(get_db)):
    response = RedirectResponse("/", status_code=303)
    logout(db, request, response)
    return response


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ensure_default_courses(db, user)
    ics_url = None
    if not is_demo_user(user):
        # Generate the calendar subscription token lazily on first settings visit.
        if not user.ics_token:
            user.ics_token = secrets.token_urlsafe(24)
            db.commit()
        ics_url = f"{get_settings().base_url.rstrip('/')}/calendar/{user.ics_token}.ics"
    # Per-institution fallbacks shown as placeholders when the user hasn't overridden.
    inst = institution_by_code(user.institution_code) or {}
    defaults = {
        "canvas_url": inst.get("canvas_url") or "",
        "ed_url": inst.get("ed_base_url") or "",
    }
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "user": user,
            "courses": user.courses,
            "extras": user.extras,
            "recurrences": user.recurring_tasks,
            "has_canvas": bool(user.canvas_token_enc),
            "has_ed": bool(user.ed_token_enc),
            "settings": get_settings(),
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
            "is_demo": is_demo_user(user),
            "institutions": institutions_choices(),
            "current_institution": user.institution_code,
            "institution_defaults": defaults,
            "ics_url": ics_url,
        },
    )


@app.post("/settings/ics/rotate")
def rotate_ics_token(request: Request, db: Session = Depends(get_db)):
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if is_demo_user(user):
        return RedirectResponse("/settings?error=demo_readonly", status_code=303)
    user.ics_token = secrets.token_urlsafe(24)
    db.commit()
    return RedirectResponse("/settings?saved=calendar", status_code=303)


@app.get("/calendar/{token}.ics")
def calendar_ics(token: str, request: Request, db: Session = Depends(get_db)):
    """Calendar-app subscription endpoint (token auth; no cookies needed)."""
    user = db.query(User).filter(User.ics_token == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="calendar not found")
    body = build_ics(db, user)
    headers = {"Content-Type": "text/calendar; charset=utf-8"}
    if request.query_params.get("dl"):
        headers["Content-Disposition"] = 'attachment; filename="dueboard.ics"'
    return Response(body, headers=headers)


@app.post("/settings/institution")
def save_institution(
    request: Request,
    db: Session = Depends(get_db),
    institution_code: str = Form(...),
    also_reset_courses: str | None = Form(None),
):
    """Switch the user's institution. Optionally reseed course defaults to the new set."""
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if is_demo_user(user):
        return RedirectResponse("/settings?error=demo_readonly", status_code=303)
    code = institution_code.strip().lower()
    if institution_by_code(code) is None:
        return RedirectResponse("/settings?error=bad_institution", status_code=303)
    switched = user.institution_code != code
    user.institution_code = code
    if switched and also_reset_courses:
        # Clear old courses and seed new institution defaults.
        for c in list(user.courses):
            db.delete(c)
        db.flush()
        ensure_default_courses(db, user)
    db.commit()
    return RedirectResponse("/settings?saved=institution", status_code=303)


@app.post("/settings/tokens")
def save_tokens(
    request: Request,
    db: Session = Depends(get_db),
    canvas_token: str = Form(""),
    canvas_api_url: str = Form(""),
    ed_token: str = Form(""),
    ed_base_url: str = Form(""),
    clear_canvas: str | None = Form(None),
    clear_ed: str | None = Form(None),
):
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if is_demo_user(user):
        return RedirectResponse("/settings?error=demo_readonly", status_code=303)
    inst = institution_by_code(user.institution_code) or {}
    if clear_canvas:
        user.canvas_token_enc = ""
    elif canvas_token.strip():
        user.canvas_token_enc = encrypt_secret(canvas_token.strip())
    # URL storage: clear the stored override so the institution default takes over
    # when the value matches the institution default. Preserve the value when the
    # user explicitly customised it.
    c_url = canvas_api_url.strip().rstrip("/")
    default_canvas = (inst.get("canvas_url") or "").rstrip("/")
    user.canvas_api_url = "" if (not c_url or c_url == default_canvas) else c_url

    if clear_ed:
        user.ed_token_enc = ""
    elif ed_token.strip():
        user.ed_token_enc = encrypt_secret(ed_token.strip())
    e_url = ed_base_url.strip().rstrip("/")
    default_ed = (inst.get("ed_base_url") or "").rstrip("/")
    user.ed_base_url = "" if (not e_url or e_url == default_ed) else e_url

    db.commit()
    return RedirectResponse("/settings?saved=tokens", status_code=303)


@app.post("/settings/reminders")
def save_reminders(
    request: Request,
    db: Session = Depends(get_db),
    email_reminders: str | None = Form(None),
    horizon_days: int = Form(3),
    morning_hour: int = Form(8),
    evening_hour: int = Form(18),
    timezone: str = Form("Australia/Sydney"),
    reminder_lead_hours: int = Form(24),
):
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if is_demo_user(user):
        return RedirectResponse("/settings?error=demo_readonly", status_code=303)
    user.email_reminders = email_reminders == "on"
    user.horizon_days = max(1, min(horizon_days, 30))
    user.morning_hour = max(0, min(morning_hour, 23))
    user.evening_hour = max(0, min(evening_hour, 23))
    user.timezone = timezone.strip() or "Australia/Sydney"
    user.reminder_lead_hours = max(0, min(reminder_lead_hours, 168))
    db.commit()
    return RedirectResponse("/settings?saved=reminders", status_code=303)


@app.post("/settings/courses")
def save_course(
    request: Request,
    db: Session = Depends(get_db),
    code: str = Form(...),
    canvas_id: str = Form(""),
    ed_id: str = Form(""),
):
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if is_demo_user(user):
        return RedirectResponse("/settings?error=demo_readonly", status_code=303)
    code = code.strip().upper()
    if not code:
        return RedirectResponse("/settings?error=course", status_code=303)
    existing = (
        db.query(UserCourse).filter(UserCourse.user_id == user.id, UserCourse.code == code).first()
    )
    cid = int(canvas_id) if canvas_id.strip().isdigit() else None
    eid = int(ed_id) if ed_id.strip().isdigit() else None
    if existing:
        existing.canvas_id = cid
        existing.ed_id = eid
    else:
        db.add(UserCourse(user_id=user.id, code=code, canvas_id=cid, ed_id=eid))
    db.commit()
    return RedirectResponse("/settings?saved=course", status_code=303)


@app.post("/settings/courses/{course_id}/delete")
def delete_course(course_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if is_demo_user(user):
        return RedirectResponse("/settings?error=demo_readonly", status_code=303)
    row = (
        db.query(UserCourse)
        .filter(UserCourse.id == course_id, UserCourse.user_id == user.id)
        .first()
    )
    if row:
        db.delete(row)
        db.commit()
    return RedirectResponse("/settings?saved=course", status_code=303)


@app.post("/settings/extras")
def add_extra(
    request: Request,
    db: Session = Depends(get_db),
    course: str = Form(...),
    title: str = Form(...),
    due_at: str = Form(...),
    url: str = Form(""),
):
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if is_demo_user(user):
        return RedirectResponse("/settings?error=demo_readonly", status_code=303)
    db.add(
        ExtraTask(
            user_id=user.id,
            course=course.strip(),
            title=title.strip(),
            due_at=due_at.strip(),
            url=url.strip(),
        )
    )
    db.commit()
    return RedirectResponse("/settings?saved=extra", status_code=303)


@app.post("/settings/extras/{extra_id}/delete")
def delete_extra(extra_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if is_demo_user(user):
        return RedirectResponse("/settings?error=demo_readonly", status_code=303)
    row = db.query(ExtraTask).filter(ExtraTask.id == extra_id, ExtraTask.user_id == user.id).first()
    if row:
        db.delete(row)
        db.commit()
    return RedirectResponse("/settings?saved=extra", status_code=303)


@app.post("/settings/recurring")
def add_recurring(
    request: Request,
    db: Session = Depends(get_db),
    course: str = Form(...),
    title: str = Form(...),
    weekday: int = Form(...),
    start_hm: str = Form(...),
    end_hm: str = Form(...),
    url: str = Form(""),
):
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if is_demo_user(user):
        return RedirectResponse("/settings?error=demo_readonly", status_code=303)
    course = course.strip().upper()
    start_hm = start_hm.strip()
    end_hm = end_hm.strip()
    if not course or not title.strip():
        return RedirectResponse("/settings?error=recurring", status_code=303)
    if not (0 <= weekday <= 6):
        return RedirectResponse("/settings?error=recurring", status_code=303)
    for hm in (start_hm, end_hm):
        try:
            datetime.strptime(hm, "%H:%M")
        except ValueError:
            return RedirectResponse("/settings?error=recurring", status_code=303)
    if end_hm <= start_hm:
        return RedirectResponse("/settings?error=recurring", status_code=303)
    db.add(
        RecurringTask(
            user_id=user.id,
            course=course,
            title=title.strip(),
            weekday=weekday,
            start_hm=start_hm,
            end_hm=end_hm,
            url=url.strip(),
        )
    )
    db.commit()
    return RedirectResponse("/settings?saved=recurring", status_code=303)


@app.post("/settings/recurring/{rec_id}/delete")
def delete_recurring(rec_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if is_demo_user(user):
        return RedirectResponse("/settings?error=demo_readonly", status_code=303)
    row = (
        db.query(RecurringTask)
        .filter(RecurringTask.id == rec_id, RecurringTask.user_id == user.id)
        .first()
    )
    if row:
        db.delete(row)
        db.commit()
    return RedirectResponse("/settings?saved=recurring", status_code=303)


@app.post("/sync")
def sync_now(request: Request, db: Session = Depends(get_db)):
    user = current_user(db, request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if is_demo_user(user):
        seed_demo_dues(db, user)
        return RedirectResponse("/?synced=demo", status_code=303)
    try:
        sync_user_dues(db, user)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/?error={quote(str(exc)[:120])}", status_code=303)
    return RedirectResponse("/?synced=1", status_code=303)


def run() -> None:
    import os

    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("web.app:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    run()
