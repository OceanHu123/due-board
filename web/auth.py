"""No-auth board: a single singleton 'board owner' user, no login needed."""

from __future__ import annotations

from sqlalchemy.orm import Session

from web.config import DEFAULT_INSTITUTION_CODE, get_settings
from web.db import User
from web.sync import ensure_default_courses

BOARD_OWNER_EMAIL = "board@due-board.local"


def get_board_user(db: Session) -> User:
    """Return the singleton board owner, creating it on first call."""
    user = db.query(User).filter(User.email == BOARD_OWNER_EMAIL).first()
    if user is None:
        user = User(
            email=BOARD_OWNER_EMAIL,
            institution_code=DEFAULT_INSTITUTION_CODE,
            email_reminders=False,
        )
        db.add(user)
        db.flush()
        ensure_default_courses(db, user)
        db.commit()
        db.refresh(user)
    return user
