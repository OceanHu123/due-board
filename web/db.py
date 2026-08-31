from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from web.config import database_url, get_settings


class Base(DeclarativeBase):
    pass



class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    canvas_token_enc: Mapped[str] = mapped_column(Text, default="")
    canvas_api_url: Mapped[str] = mapped_column(
        String(512), default="https://canvas.sydney.edu.au/api/v1"
    )
    ed_token_enc: Mapped[str] = mapped_column(Text, default="")
    ed_base_url: Mapped[str] = mapped_column(String(512), default="https://edstem.org/api")

    email_reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    horizon_days: Mapped[int] = mapped_column(Integer, default=3)
    morning_hour: Mapped[int] = mapped_column(Integer, default=8)
    evening_hour: Mapped[int] = mapped_column(Integer, default=18)
    timezone: Mapped[str] = mapped_column(String(64), default="Australia/Sydney")

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_error: Mapped[str] = mapped_column(Text, default="")
    last_morning_email_date: Mapped[str] = mapped_column(String(16), default="")
    last_evening_email_date: Mapped[str] = mapped_column(String(16), default="")

    courses: Mapped[list[UserCourse]] = relationship(back_populates="user", cascade="all, delete-orphan")
    dues: Mapped[list[DueCache]] = relationship(back_populates="user", cascade="all, delete-orphan")
    extras: Mapped[list[ExtraTask]] = relationship(back_populates="user", cascade="all, delete-orphan")


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


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
