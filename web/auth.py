from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException, Request, Response
from sqlalchemy.orm import Session

from web.config import get_settings
from web.db import MagicLink, Session as DbSession, User
from web.mailer import send_email
from web.sync import ensure_default_courses

COOKIE = "due_board_session"
log = logging.getLogger("due_board.auth")


def _external_base_url(request: Request | None = None) -> str:
    """Best-effort production-aware base URL.

    Priority: request host (respects Render's X-Forwarded-Host), then the
    configured BASE_URL, then the hard-coded localhost fallback.
    """
    if request is not None:
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        scheme = request.headers.get("x-forwarded-proto") or "https"
        if host:
            return f"{scheme.rstrip(':')}://{host}"
    return get_settings().base_url.rstrip("/")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _set_session_cookie(response: Response, session_raw: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE,
        session_raw,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_hours * 3600,
    )


def create_session(db: Session, user: User, response: Response) -> None:
    settings = get_settings()
    session_raw = secrets.token_urlsafe(32)
    db.add(
        DbSession(
            user_id=user.id,
            token_hash=_hash(session_raw),
            expires_at=datetime.now(timezone.utc)
            + timedelta(hours=settings.session_hours),
        )
    )
    db.commit()
    _set_session_cookie(response, session_raw)


def request_magic_link(db: Session, email: str, request: Request | None = None) -> str:
    """Create a magic link row, email it, and always return the absolute URL.

    The returned URL doubles as the dev shortcut shown on the login page.
    We build it from the incoming request's host headers so it works in prod
    even when BASE_URL isn't explicitly set.
    """
    settings = get_settings()
    email = email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Invalid email")
    if email == settings.demo_email.strip().lower():
        raise HTTPException(400, "Use Try demo on the home page instead")
    raw = secrets.token_urlsafe(32)
    link = MagicLink(
        email=email,
        token_hash=_hash(raw),
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=settings.magic_link_minutes),
    )
    db.add(link)
    db.commit()
    url = f"{_external_base_url(request)}/auth/verify?token={raw}"
    try:
        send_email(
            email,
            f"Sign in to {settings.app_name}",
            f"Click to sign in (expires in {settings.magic_link_minutes} minutes):\n\n{url}\n",
            html_body=f'<p>Click to sign in:</p><p><a href="{url}">{url}</a></p>',
        )
    except Exception:  # noqa: BLE001 — mail failure must not block sign-in
        log.exception("failed to send magic-link email to %s", email)
    return url


def verify_magic_link(db: Session, raw_token: str, response: Response) -> User:
    row = (
        db.query(MagicLink)
        .filter(MagicLink.token_hash == _hash(raw_token), MagicLink.used.is_(False))
        .first()
    )
    if not row:
        raise HTTPException(400, "Magic link invalid or expired")
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        raise HTTPException(400, "Magic link invalid or expired")
    row.used = True
    user = db.query(User).filter(User.email == row.email).first()
    if not user:
        user = User(email=row.email)
        db.add(user)
        db.flush()
        ensure_default_courses(db, user)
    create_session(db, user, response)
    return user


def current_user(db: Session, request: Request) -> User | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    row = db.query(DbSession).filter(DbSession.token_hash == _hash(raw)).first()
    if not row:
        return None
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        return None
    return db.query(User).filter(User.id == row.user_id).first()


def require_user(db: Session, request: Request) -> User:
    user = current_user(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def logout(db: Session, request: Request, response: Response) -> None:
    raw = request.cookies.get(COOKIE)
    if raw:
        db.query(DbSession).filter(DbSession.token_hash == _hash(raw)).delete()
        db.commit()
    response.delete_cookie(COOKIE, httponly=True, samesite="lax", secure=get_settings().cookie_secure)
