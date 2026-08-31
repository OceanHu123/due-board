from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dues_lib import DueItem, window_filter


def _item(course: str, title: str, due: datetime, source: str = "canvas") -> DueItem:
    return DueItem(course=course, title=title, due=due, source=source)


def test_window_filter_horizon_and_past():
    tz = ZoneInfo("Australia/Sydney")
    now = datetime(2026, 8, 29, 12, 0, tzinfo=tz)
    items = [
        _item("A", "past", now - timedelta(hours=1)),
        _item("A", "today", now + timedelta(hours=2)),
        _item("A", "in_horizon", now + timedelta(days=2)),
        _item("A", "outside", now + timedelta(days=10)),
    ]
    out = window_filter(items, now, horizon_days=3, tonight=False)
    titles = [i.title for i in out]
    assert titles == ["today", "in_horizon"]


def test_window_filter_tonight_only():
    tz = ZoneInfo("Australia/Sydney")
    now = datetime(2026, 8, 29, 18, 0, tzinfo=tz)
    items = [
        _item("A", "today", now + timedelta(hours=1)),
        _item("A", "tomorrow", now + timedelta(days=1)),
    ]
    out = window_filter(items, now, horizon_days=3, tonight=True)
    assert [i.title for i in out] == ["today"]


def test_exclude_drill_title_helper():
    from dues_lib import _excluded

    assert _excluded("Week 1 Drills", ("Drill",))
    assert not _excluded("Week 1 Task", ("Drill",))
