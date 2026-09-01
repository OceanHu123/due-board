# Deploy DueBoard on Render（不用买域名）

目标：得到一个类似 `https://due-board.onrender.com` 的网址，**收藏后随时打开**（电脑关机也能进）。  
**不需要购买自定义域名**——和 GitHub Pages 的 `*.github.io` 一样，用平台送的子域名即可。

> 原项目名：**Usyd Due**。如果你部署的是旧版本，文档中的路径/名称可替换为 `usyd-due-web` 等旧值。

---

## 0. 你需要有的东西

- [ ] GitHub 账号  
- [ ] 本仓库已 **commit + push** 到 GitHub（下面第 1 步）  
- [ ] [Render](https://render.com) 账号（可用 GitHub 登录）  
- [ ] （可选）[Resend](https://resend.com)——只要看板、不要云端邮件可跳过  

本机提醒（launchd）和云端网站可以同时保留；网站负责「随时打开看板」。

---

## 1. 把代码推到 GitHub

在项目目录：

```sh
cd ~/Projects/due-board

# 若还没有 git 远程：在 GitHub 网页 New repository（不要勾选自动加 README），然后：
git init   # 若已是 git 仓库可跳过
git add .
git status   # 确认没有把 .env 加进去
git commit -m "Add DueBoard web platform for Render deploy"
git branch -M main
git remote add origin https://github.com/OceanHu123/due-board.git
git push -u origin main
```

**不要**把 `.env`、Canvas/Ed token 推上去（仓库已忽略 `.env`）。

---

## 2. 本地生成 Fernet 密钥（复制保存）

```sh
cd ~/Projects/due-board
uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

把输出整行复制好，等下填到 Render 的 `TOKEN_FERNET_KEY`。

---

## 3. 用 Blueprint 一键创建服务

> **免费套餐注意：** Render Free **没有 Background Worker**。仓库里的 `render.yaml` 现在只创建 **Web + Postgres**。定时邮件可先用本机；看板靠网页上点「刷新」。

1. 打开 [https://dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**
2. 连接 GitHub，选中 `due-board` 仓库
3. Render 会读 [`render.yaml`](../render.yaml)，创建：
   - **Web**：`due-board`
   - **Postgres**：`due-board-db`
4. 填环境变量（见下一节）→ **Apply**

若你之前 Blueprint 已失败过：在 Blueprint 页点 **Manual sync**（或删掉失败的 Web 服务后重新 Apply），确保拉到最新 `render.yaml`（已去掉 Worker）。

部署完成后，Web 服务页面会显示例如：

`https://due-board-xxxx.onrender.com`

**收藏这个地址。**

### Web 仍 Failed deploy 时

打开 **due-board → Logs**，常见原因：

| 日志线索 | 处理 |
|----------|------|
| database / SSL / connection | 已在代码里为 Render 自动加 `sslmode=require`；拉最新代码再 deploy |
| health check failed | 等冷启动完成；确认 `healthCheckPath` 为 `/healthz` |
| TOKEN_FERNET_KEY / SECRET_KEY | 在 Environment 里补上后 **Clear build cache & deploy** |

只要看板：**不必**开 Worker，也不必买域名。

---

## 4. 环境变量怎么填

在 **due-board** 的 Environment 里：


| 变量                 | 填什么                                                               | 必填？         |
| ------------------ | ----------------------------------------------------------------- | ----------- |
| `BASE_URL`         | 可选，作 cookie secure 判定的 fallback；dev_link 自动从 Render header 构建     | 建议填         |
| `SECRET_KEY`       | Blueprint 可自动生成；或自己一长串随机字符                                        | 是           |
| `TOKEN_FERNET_KEY` | 第 2 步生成的 Fernet 密钥                                                | 是           |
| `DATABASE_URL`     | Blueprint 从 Postgres 自动挂上即可                                       | 是（自动）       |
| `REQUIRE_MAIL`     | 只要看板：填 `false`                                                     | 建议 `false`  |
| `RESEND_API_KEY`   | 仅在要发邮件时填（Resend 免费额度够个人用）                                          | 否           |
| `SMTP_FROM`        | 仅发邮件时填（如 `DueBoard <onboarding@resend.dev>`）                           | 否           |
| `GITHUB_URL`       | 你的 GitHub 仓库链接（页脚用）                                               | 否           |

### 登录 magic link 怎么拿

**不配邮件也能登录：** 点 Send magic link 后，登录页底部会显示一个 **Direct sign-in link**，点进去就能进（有效期 20 分钟）。这个链接的域名和当前页面完全一致，Render 上不会再出现 localhost 的问题。

**想让 magic link 真的发到邮箱：** 注册 [Resend](https://resend.com)（免费额度 3000 封/天），拿到 API Key 填到 Render 的 `RESEND_API_KEY`，再把 `SMTP_FROM` 设成 `DueBoard <onboarding@resend.dev>`（Resend 测试域名只能发到你自己注册用的邮箱）。这样既会发邮件，也仍然在登录页保留 Direct sign-in link 作安全网。

---

## 5. 部署后自测清单

1. 浏览器打开 `https://…onrender.com` → 能看到 Landing
2. **Try demo** → 能进看板
3. 用自己的邮箱登录 → 设置里粘贴 Canvas / Ed token → **刷新未完成 due**
4. 手机流量下再打开同一网址（确认不是只有你家 Wi‑Fi 能开）
5. 电脑关机，用手机再开一次收藏夹

---

## 6. Free 套餐你要知道的坑

- **Web 会休眠**：一段时间没人访问后，下一次打开可能要等 **30–60 秒** 冷启动，属正常。  
- **Postgres free**：有用量/时效限制；若创建失败，看 Render 是否还提供免费库，或改用他们当前的免费数据库方案。  
- **Worker**：用于后台同步/邮件；只要看板也可以先只开 Web，需要时再开 Worker。  
- 改代码后：`git push`，Render 一般会自动重新部署。

---

## 7. 和本机工具怎么分工


|      | 本机 `due-board` + launchd | Render 网站             |
| ---- | ------------------------ | --------------------- |
| 电脑关机 | 到点可能没通知                  | 看板仍可打开                |
| 横幅通知 | 有                        | 无（除非以后再开邮件）           |
| 收藏网址 | `127.0.0.1` 仅本机服务开着时可用   | `*.onrender.com` 随时可用 |


---

## 8. 常见问题

**Q: 必须买域名吗？**  
A: 不必。用 `*.onrender.com` 即可。

**Q: 为什么和 GitHub Pages 一样不用买域名，但不能把这个站丢到 Pages？**  
A: GitHub Pages 适合静态网页。本项目是 **FastAPI + 数据库**，需要 Render/Railway 这类能跑后端的平台。

**Q: 打开很慢？**  
A: Free Web 休眠后冷启动。可先点一下等加载；或以后升级付费 always-on。

**Q: 登录收不到邮件？**  
A: 先设 `REQUIRE_MAIL=false`，看登录页是否直接给出 magic link；或配 Resend 后发到注册邮箱。

**Q: Token 安全吗？**  
A: 存在云端数据库里（Fernet 加密）。只用你自己的 Render 账号；可随时在 Canvas/Ed 撤销 token。

**Q: 我是其他学校（UNSW / Monash / 自定义）的？**  
A: 登录后在 **Settings → Institution** 切换，默认 Canvas / Ed URL 会自动调整。

---

## 9. 部署成功后记得做

1. 把 Live URL 写进简历 / README 顶部
2. 浏览器收藏该 URL
3. （可选）把本机 launchd 留作「开机横幅」备份

若某一步报错，把 Render 的 **Deploy logs** 最后 30 行复制出来即可继续排查。