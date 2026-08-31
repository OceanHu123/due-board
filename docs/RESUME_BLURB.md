# Resume blurbs — DueBoard (formerly Usyd Due)

## English (pick 3–4)

- Built a **multi-tenant FastAPI** service that aggregates University LMS deadlines from **Canvas and Ed APIs**, with per-user Fernet-encrypted credential storage and Sydney-timezone email digests.
- Designed a shared **domain library** (`dues_lib`) for due fetching/filtering reused by the web worker and an optional local macOS notifier — one rule set, two clients.
- Implemented **magic-link auth**, due board UI, background sync worker, and a public **demo path** with seeded data so recruiters can try the product without LMS tokens.
- Deployed with **Postgres + Docker on Render**, health checks, Secure cookies over HTTPS, and CI (`pytest` + GitHub Actions).

## 中文（可选）

- 用 FastAPI 做多租户 due 看板：对接 Canvas / Ed 官方 API，用户自备 token，Fernet 加密落库，Sydney 时区早晚邮件提醒。
- 抽出共用 `dues_lib`（过滤 Drill、占位作业、已提交项），Web worker 与本机工具共用同一套规则。
- 提供公网 Demo（虚构 due）、Privacy 说明、Render 部署与自动化测试，方便简历展示与面试讲解。

## One-liner

Multi-tenant Canvas/Ed due board with encrypted tokens and timezone-aware email digests.
