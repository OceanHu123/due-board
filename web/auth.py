from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response
from sqlalchemy.orm import Session

from web.config import get_settings
from web.db import MagicLink, Session as DbSession, User
from web.mailer import send_email
from web.sync import ensure_default_courses

COOKIE = "due_board_session"


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


def request_magic_link(db: Session, email: str) -> str:
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
    url = f"{settings.base_url.rstrip('/')}/auth/verify?token={raw}"
    send_email(
        email,
        f"Sign in to {settings.app_name}",
        f"Click to sign in (expires in {settings.magic_link_minutes} minutes):\n\n{url}\n",
        html_body=f'<p>Click to sign in:</p><p><a href="{url}">{url}</a></p>',
    )
    return url if not settings.mail_configured else ""


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
