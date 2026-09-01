from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from web.config import DEFAULT_INSTITUTION_CODE, database_url, get_settings


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # Multi-institution support: defaults to the server-level default (usually 'usyd').
    institution_code: Mapped[str] = mapped_column(
        String(32), default=DEFAULT_INSTITUTION_CODE, index=True
    )

    canvas_token_enc: Mapped[str] = mapped_column(Text, default="")
    canvas_api_url: Mapped[str] = mapped_column(
        String(512), default=""  # per-user override; default comes from institution
    )
    ed_token_enc: Mapped[str] = mapped_column(Text, default="")
    ed_base_url: Mapped[str] = mapped_column(String(512), default="")

    email_reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    horizon_days: Mapped[int] = mapped_column(Integer, default=3)
    morning_hour: Mapped[int] = mapped_column(Integer, default=8)
    evening_hour: Mapped[int] = mapped_column(Integer, default=18)
    timezone: Mapped[str] = mapped_column(String(64), default="Australia/Sydney")

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_error: Mapped[str] = mapped_column(Text, default="")
    last_morning_email_date: Mapped[str] = mapped_column(String(16), default="")
    last_evening_email_date: Mapped[str] = mapped_column(String(16), default="")

    # Smart pre-deadline reminder: email once per day when dues fall inside the lead window.
    reminder_lead_hours: Mapped[int] = mapped_column(Integer, default=24)  # 0 = off
    last_lead_reminder_date: Mapped[str] = mapped_column(String(16), default="")

    # Per-user calendar subscription token (calendar apps cannot send cookies).
    ics_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)

    courses: Mapped[list[UserCourse]] = relationship(back_populates="user", cascade="all, delete-orphan")
    dues: Mapped[list[DueCache]] = relationship(back_populates="user", cascade="all, delete-orphan")
    extras: Mapped[list[ExtraTask]] = relationship(back_populates="user", cascade="all, delete-orphan")
    recurring_tasks: Mapped[list[RecurringTask]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserCourse(Base):
    __tablename__ = "user_courses"
    __table_args__ = (UniqueConstraint("user_id", "code", name="uq_user_course_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    canvas_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ed_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    user: Mapped[User] = relationship(back_populates="courses")


class ExtraTask(Base):
    __tablename__ = "extra_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    course: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    due_at: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(1024), default="")

    user: Mapped[User] = relationship(back_populates="extras")


class RecurringTask(Base):
    """Weekly recurring time block, e.g. 'Mon 18:00-19:00 project meeting'."""

    __tablename__ = "recurring_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    course: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Monday … 6 = Sunday
    start_hm: Mapped[str] = mapped_column(String(5))  # "HH:MM" (user-local)
    end_hm: Mapped[str] = mapped_column(String(5))
    url: Mapped[str] = mapped_column(String(1024), default="")

    user: Mapped[User] = relationship(back_populates="recurring_tasks")


class DueCache(Base):
    __tablename__ = "due_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    course: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(String(1024), default="")
    detail: Mapped[str] = mapped_column(String(256), default="")

    user: Mapped[User] = relationship(back_populates="dues")


class MagicLink(Base):
    __tablename__ = "magic_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def make_engine():
    from pathlib import Path

    url = database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, connect_args=connect_args)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def reconfigure_db() -> None:
    """Rebuild engine after env/settings change (tests)."""
    global engine, SessionLocal
    get_settings.cache_clear()
    engine.dispose()
    engine = make_engine()
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _migrate_schema() -> None:
    """Idempotent lightweight migrations: create_all never alters pre-existing tables."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if insp.has_table("users"):
        cols = {c["name"] for c in insp.get_columns("users")}
        adds: list[str] = []
        if "institution_code" not in cols:
            adds.append(
                "ALTER TABLE users ADD COLUMN institution_code VARCHAR(32) "
                f"NOT NULL DEFAULT '{DEFAULT_INSTITUTION_CODE}'"
            )
            adds.append("CREATE INDEX ix_users_institution_code ON users (institution_code)")
        if "reminder_lead_hours" not in cols:
            adds.append("ALTER TABLE users ADD COLUMN reminder_lead_hours INTEGER NOT NULL DEFAULT 24")
        if "last_lead_reminder_date" not in cols:
            adds.append("ALTER TABLE users ADD COLUMN last_lead_reminder_date VARCHAR(16) NOT NULL DEFAULT ''")
        if "ics_token" not in cols:
            adds.append("ALTER TABLE users ADD COLUMN ics_token VARCHAR(64)")
            adds.append("CREATE INDEX ix_users_ics_token ON users (ics_token)")
        if adds:
            with engine.begin() as conn:
                for stmt in adds:
                    conn.execute(text(stmt))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_schema()
