#!/usr/bin/env python3
"""Local macOS Canvas + Ed due reminders (optional personal tooling)."""

from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from dues_lib import (
    DEFAULT_EXCLUDE,
    CanvasCreds,
    CourseRef,
    DueItem,
    EdCreds,
    collect_dues,
    window_filter,
)

ROOT = Path(__file__).resolve().parent


def load_dotenv_file(path: str | Path) -> None:
    p = Path(path).expanduser()
    if not p.is_file():
        raise SystemExit(f"Missing env file: {p}")
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def courses_from_cfg(cfg: dict[str, Any]) -> list[CourseRef]:
    out: list[CourseRef] = []
    for row in cfg.get("courses") or []:
        out.append(
            CourseRef(
                code=str(row["code"]),
                canvas_id=int(row["canvas_id"]) if row.get("canvas_id") is not None else None,
                ed_id=int(row["ed_id"]) if row.get("ed_id") is not None else None,
            )
        )
    return out


def dues_page_path(cfg: dict[str, Any]) -> Path:
    state = Path(cfg["state_file"]).expanduser()
    return state.parent / "dues.html"


def write_due_page(path: Path, items: list[DueItem], now: datetime, mode: str, horizon: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode_label = "今晚未交（当天）" if mode == "tonight" else f"今天 + 未来 {horizon} 天"
    generated = now.strftime("%Y-%m-%d %H:%M %Z")

    if not items:
        body = '<p class="empty">这个窗口里没有未完成的 due。</p>'
    else:
        cards: list[str] = []
        for item in items:
            when = item.due.strftime("%a %-d %b %Y · %-I:%M %p").replace("AM", "am").replace("PM", "pm")
            today = item.due.date() == now.date()
            badge = "今天" if today else item.due.strftime("%-d %b")
            link = ""
            if item.url:
                safe = html.escape(item.url, quote=True)
                link = f'<a class="open" href="{safe}" target="_blank" rel="noopener">打开</a>'
            detail = html.escape(item.detail or item.source)
            cards.append(
                f"""
<article class="card {'today' if today else ''}">
  <div class="meta">
    <span class="course">{html.escape(item.course)}</span>
    <span class="badge">{html.escape(badge)}</span>
  </div>
  <h2>{html.escape(item.title)}</h2>
  <p class="due"><strong>截止</strong> {html.escape(when)}</p>
  <p class="remain">{html.escape(item.remaining(now))}</p>
  <p class="src">{detail}</p>
  {link}
</article>"""
            )
        body = "\n".join(cards)

    doc = f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Usyd Due · {html.escape(mode_label)}</title>
<style>
  :root {{
    --bg: #f4f1ea; --ink: #1c1917; --muted: #57534e; --card: #fffdf8;
    --line: #e7e5e4; --accent: #0f766e; --today: #b45309;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: "SF Pro Text", "PingFang SC", "Helvetica Neue", sans-serif;
    background:
      radial-gradient(1200px 600px at 10% -10%, #dce9e6 0%, transparent 55%),
      radial-gradient(900px 500px at 100% 0%, #f3e7d5 0%, transparent 50%),
      var(--bg);
    color: var(--ink); line-height: 1.45;
  }}
  main {{ max-width: 720px; margin: 0 auto; padding: 2.25rem 1.25rem 3rem; }}
  header h1 {{ font-size: 1.75rem; font-weight: 700; margin: 0 0 0.35rem; }}
  header p {{ margin: 0; color: var(--muted); font-size: 0.95rem; }}
  .count {{
    display: inline-block; margin-top: 1rem; padding: 0.25rem 0.65rem;
    border-radius: 999px; background: #ecfdf5; color: var(--accent);
    font-size: 0.85rem; font-weight: 600;
  }}
  .list {{ display: grid; gap: 0.9rem; margin-top: 1.5rem; }}
  .card {{
    background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    padding: 1rem 1.1rem;
  }}
  .card.today {{ border-color: #fdba74; box-shadow: inset 3px 0 0 var(--today); }}
  .meta {{ display: flex; justify-content: space-between; margin-bottom: 0.35rem; }}
  .course {{ font-size: 0.8rem; font-weight: 700; color: var(--accent); }}
  .badge {{
    font-size: 0.75rem; font-weight: 600; color: var(--today);
    background: #fff7ed; padding: 0.15rem 0.5rem; border-radius: 999px;
  }}
  h2 {{ margin: 0 0 0.55rem; font-size: 1.12rem; }}
  .due, .remain {{ margin: 0.2rem 0; font-size: 0.92rem; }}
  .src {{ color: var(--muted); font-size: 0.82rem; }}
  .open {{
    display: inline-block; margin-top: 0.7rem; color: white; background: var(--accent);
    text-decoration: none; font-size: 0.88rem; font-weight: 600;
    padding: 0.45rem 0.85rem; border-radius: 8px;
  }}
  .empty {{
    margin-top: 1.5rem; padding: 1.25rem; background: var(--card);
    border-radius: 12px; border: 1px dashed var(--line); color: var(--muted);
  }}
  footer {{ margin-top: 2rem; font-size: 0.78rem; color: var(--muted); }}
</style>
</head>
<body>
<main>
  <header>
    <h1>Usyd Due</h1>
    <p>{html.escape(mode_label)} · 生成于 {html.escape(generated)}</p>
    <span class="count">{len(items)} 项未交</span>
  </header>
  <section class="list">{body}</section>
  <footer>本机可选工具。多用户请用 Web 平台：<code>uv run usyd-due-web</code></footer>
</main>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")
    return path


NOTIFY_APP_CANDIDATES = [
    Path.home() / "Applications" / "UsydDueReminders.app" / "Contents" / "MacOS" / "UsydDueReminders",
    ROOT / "UsydDueReminders.app" / "Contents" / "MacOS" / "UsydDueReminders",
]


def notify_binary() -> Path | None:
    for path in NOTIFY_APP_CANDIDATES:
        if path.is_file():
            return path
    return None


def notify(title: str, body: str, dry_run: bool, page: Path | None = None) -> None:
    if dry_run:
        print(f"[notify] {title}\n{body}\n")
        if page:
            print(f"[page] {page}")
        return

    binary = notify_binary()
    if binary is not None:
        cmd = [str(binary), title[:60], body[:220]]
        if page is not None:
            cmd.append(str(page))
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            return
        err = (result.stderr or result.stdout or "").strip()
        if err:
            print(err, file=sys.stderr)

    if page is not None and page.is_file():
        subprocess.run(["open", str(page)], check=False)

    script = (
        f"display notification {json.dumps(body[:220], ensure_ascii=False)} "
        f"with title {json.dumps(title[:60], ensure_ascii=False)} "
        f'sound name "Glass"'
    )
    subprocess.run(["osascript", "-e", script], check=False)


def format_body(items: list[DueItem], now: datetime) -> str:
    if not items:
        return "未来几天没有到期项。点开可看详情页。"
    head = f"{len(items)} 项 due · 点开看详情"
    first = items[0].line(now)
    text = f"{head}\n{first}"
    if len(items) > 1:
        text += f"\n另有 {len(items) - 1} 项…"
    return text[:220]


def read_last_run(path: Path) -> datetime | None:
    if not path.is_file():
        return None
    try:
        return datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def write_last_run(path: Path, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(now.isoformat(), encoding="utf-8")


def decide_mode(now: datetime, last: datetime | None, catch_up_hours: float, forced: str | None) -> str:
    if forced:
        return forced
    if last is None or (now - last).total_seconds() >= catch_up_hours * 3600:
        return "summary"
    if now.hour >= 18:
        return "tonight"
    return "summary"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Usyd Canvas + Ed due reminders (local optional)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--mode", choices=("auto", "summary", "tonight"), default="auto")
    args = parser.parse_args(argv)

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    page_path = dues_page_path(cfg)

    if args.test:
        if not page_path.is_file():
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_text(
                "<!DOCTYPE html><meta charset=utf-8><title>Usyd Due</title>"
                "<body style='font-family:sans-serif;padding:2rem'>"
                "<h1>Usyd Due 测试页</h1></body>",
                encoding="utf-8",
            )
        notify("Usyd Due 测试", "点这条通知应打开 due 详情页。", args.dry_run, page=page_path)
        return 0

    tz = ZoneInfo(cfg.get("timezone") or "Australia/Sydney")
    now = datetime.now(tz)
    horizon = int(cfg.get("horizon_days") or 3)
    state_path = Path(cfg["state_file"]).expanduser()
    last = read_last_run(state_path)
    forced = None if args.mode == "auto" else args.mode
    mode = decide_mode(now, last, float(cfg.get("catch_up_hours") or 20), forced)

    load_dotenv_file(cfg["canvas_env"])
    load_dotenv_file(cfg["ed_env"])
    canvas = CanvasCreds(
        token=os.environ.get("CANVAS_API_TOKEN", ""),
        api_url=os.environ.get("CANVAS_API_URL", "https://canvas.sydney.edu.au/api/v1"),
    )
    ed = EdCreds(
        token=os.environ.get("ED_API_TOKEN", ""),
        base_url=os.environ.get("ED_BASE_URL", "https://edstem.org/api"),
    )
    exclude = cfg.get("exclude_title_substrings") or list(DEFAULT_EXCLUDE)
    collected = collect_dues(
        canvas=canvas,
        ed=ed,
        courses=courses_from_cfg(cfg),
        tz=tz,
        exclude=exclude,
        extras=cfg.get("extra_tasks") or [],
    )
    tonight = mode == "tonight"
    due = window_filter(collected, now, horizon, tonight=tonight)
    write_due_page(page_path, due, now, mode, horizon)

    if args.open:
        subprocess.run(["open", str(page_path)], check=False)
        print(page_path)
        return 0

    if not due:
        if args.dry_run:
            print("未来几天没有到期项。" if not tonight else "今晚没有未过期的当天 due。")
            print(f"mode={mode} count=0 page={page_path}")
        else:
            write_last_run(state_path, now)
        return 0

    title = "今晚还没交" if tonight else "未来几天 due"
    notify(title, format_body(due, now), args.dry_run, page=page_path)
    if args.dry_run:
        print(f"mode={mode} count={len(due)} page={page_path}")
        for item in due:
            print(f"- {item.source} {item.line(now)}")
    else:
        write_last_run(state_path, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
