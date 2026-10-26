# PanWatch 部署实操指南

> 基于仓库 `TNT-Likely/PanWatch` 的 `Makefile` / `Dockerfile` / `README` 实测内容整理。
> 适用场景：自托管一个本地/内网的 AI 股票盯盘服务（A股/港股/美股）。
> 两种路线：**A. Docker 一键（推荐，最省事）**；**B. 本地开发（需要 Python 3.11+ 与 Node 24 / pnpm 9）**。

---

## 0. 前置说明（重要）

- **单机 / 内网优先**：PanWatch 的 CORS 在 `application.py` 中是 `allow_origins=["*"]` 全开，适合自托管内网。**公网暴露务必加反向代理 + 鉴权 + 收紧 CORS**，否则任何人可访问你的接口与数据。
- **鉴权是单用户 JWT**：首次通过环境变量 `AUTH_USERNAME` / `AUTH_PASSWORD` 设定账户（或启动后用 `/api/auth` 注册/改密）。
- **AI 能力需要 API Key**：盯盘/分析依赖 LLM，需在设置里配置 AI Service（OpenAI 兼容协议，可接任意兼容端点）。
- **TradingAgents 深度分析是可选软依赖**：上游 `tradingagents` 库不在 PyPI，需自行 `git clone + pip install -e`，未装不影响核心盯盘。
- **数据目录**：所有状态（SQLite、日志、Playwright 浏览器、ca-bundle）落在 `DATA_DIR`（Docker 内为 `/app/data`）。**务必挂载卷持久化**。

---

## A. Docker 一键部署（推荐）

### A.1 最小启动

```bash
docker run -d \
  --name panwatch \
  -e TZ=Asia/Shanghai \
  -p 8000:8000 \
  -v panwatch_data:/app/data \
  sunxiaoye0721/panwatch:latest
```

- 镜像：`sunxiaoye0721/panwatch:latest`（多阶段构建：node:24 前端 + python:3.11-slim 运行）。
- 端口：后端 `8000`（Docker/本地统一，避免老用户升级困惑）。
- 时区：`-e TZ=Asia/Shanghai`（默认即此，显式指定更稳）。
- 数据卷：`panwatch_data:/app/data` —— **不挂卷则容器删除后所有数据丢失**。
- 健康检查：镜像内置 `HEALTHCHECK` 打 `/api/health`，`docker ps` 可见 `healthy` 状态。

### A.2 首次访问与初始化

1. 浏览器打开 `http://<机器IP>:8000`。
2. **设置管理员账户**：
   - 方式一（推荐，启动即生效）：在 `docker run` 加 `-e AUTH_USERNAME=admin -e AUTH_PASSWORD=你的密码`。
   - 方式二：首次进入后按引导（onboarding）创建账户。
3. 进入「设置 → AI Service」配置 LLM（填写 base_url / api_key / model，OpenAI 兼容即可）。
4. 在「数据源 / 行情」里确认行情 provider（腾讯/东财/新浪/雪球/Stooq/Yahoo/YFinance/同花顺/财联社）可用；如需代理，设置 `http_proxy`。
5. 添加自选股 → 创建/启用 Agent（盘前展望、盘中监控、收盘日报、TradingAgents 深度分析）→ 系统按交易日历自动调度。

### A.3 常用运维

```bash
# 看日志
docker logs -f panwatch

# 重启
docker restart panwatch

# 升级（拉新镜像后重建，卷保留数据）
docker pull sunxiaoye0721/panwatch:latest
docker rm -f panwatch
# 重新执行 A.1 的 docker run（卷不变）

# 备份数据（卷内 SQLite 等）
docker run --rm -v panwatch_data:/data -v $PWD:/backup alpine \
  tar czf /backup/panwatch-data-$(date +%F).tar.gz -C /data .
```

### A.4 反向代理（公网暴露必做）

Nginx 示例（HTTPS + 限来源）：

```nginx
server {
    listen 443 ssl;
    server_name panwatch.yourdomain.com;
    ssl_certificate     /path/fullchain.pem;
    ssl_certificate_key /path/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        # WebSocket（进度/预览）必须透传
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

> 公网务必：① 仅允许可信 IP 或加额外 Basic Auth；② 在 `application.py` 收紧 CORS（改 `allow_origins` 为你的域名）；③ 强密码 + 定期改密。

---

## B. 本地开发部署（源码跑）

### B.1 环境要求

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| Python | **3.11+** | 后端（`server.py` 经 uvicorn 启动） |
| Node.js | **24.x** | 前端构建/运行 |
| pnpm | **9.x** | 前端包管理（`corepack enable` 可装） |
| git | 任意 | 仅 TradingAgents 软依赖需要 |

### B.2 克隆与后端

```bash
git clone https://github.com/TNT-Likely/PanWatch.git
cd PanWatch

# 后端（等价 make dev-api）
make setup-backend        # 建 venv + pip install -r requirements.txt
make dev-api              # DEV_RELOAD=1 跑 python server.py，监听 :8000
```

或手动：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
DEV_RELOAD=1 python server.py
```

- 后端默认 `http://localhost:8000`。
- 首次启动会：`seed_sample_stocks`（5 只示例股）、`seed_agents`（内置 Agent）、`init_auth_from_env`（若设了环境变量）。
- Playwright 浏览器首次启动自动装到 `data/`（K线截图用）；`PLAYWRIGHT_SKIP_BROWSER_INSTALL=1` 可跳过。

### B.3 前端

```bash
make dev-web              # cd frontend && pnpm install && pnpm dev，监听 :5183
```

或手动：

```bash
cd frontend
corepack enable && corepack prepare pnpm@9.15.9 --activate   # 确保 pnpm 9
pnpm install --no-frozen-lockfile
pnpm dev                  # 开发服务器 :5183
```

- 前端 `:5183`（与 BeeCount-Cloud 的 `:5173` 错开）。
- 生产构建（`make build VERSION=x`）：前端 `pnpm build` → `dist/`，由 Docker 多阶段复制进镜像的 `static/`；本地可 `pnpm build` 后由后端 `server.py` 的 SPA 兜底路由托管。

### B.4 可选：启用 TradingAgents 深度分析

```bash
# 在 venv/容器内
git clone <TauricResearch/TradingAgents 仓库> external/tradingagents
pip install -e external/tradingagents
```

- 未安装时 `TradingAgentsAgent.run()` 返回明确错误、**不 crash** 服务，核心盯盘功能不受影响。

---

## C. 配置速查

| 配置项 | 位置 | 说明 |
| --- | --- | --- |
| `AUTH_USERNAME` / `AUTH_PASSWORD` | 环境变量 / `AppSettings` | 首个管理员账户（Docker 用 `-e` 注入） |
| `JWT_SECRET` | 环境变量 / `AppSettings` | JWT 签名密钥；不设置则首次自动生成并持久化 |
| `TZ` | 环境变量 | 时区，默认 `Asia/Shanghai` |
| `DATA_DIR` | 环境变量（Docker 内 `/app/data`） | 数据根目录 |
| `http_proxy` | `AppSettings` | 通知/行情出网代理 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 环境变量 | 配了才启用 OpenTelemetry 导出，否则 no-op |
| `DOCKER=1` | 自动（容器内） | 控制 Playwright 浏览器安装位置 |

---

## D. 故障排查

| 现象 | 排查 |
| --- | --- |
| 启动后页面空白 / 404 | 前端未构建或未挂载；Docker 版应已内置 `static/`，本地需 `pnpm build` 或跑 `dev-web` |
| 登录失败 | 确认是否首次设置了 `AUTH_USERNAME/PASSWORD`；JWT secret 变更会使旧 token 失效 |
| 行情拉取失败 | 检查网络能否访问对应 provider；企业网设置 `http_proxy`；`make doctor` 可做系统自检 |
| `/api/health` 不通 | 服务未起或端口被占；Docker 看 `docker logs` 与 `HEALTHCHECK` |
| TradingAgents 报错 | 上游库未 `pip install -e`；属可选能力，不影响其余功能 |
| 公网能访问但无鉴权 | 收紧 CORS + 加反向代理鉴权（见 A.4） |

> 官方自检命令：`make doctor`（数据源 / AI / 通知 / DB / 磁盘 / 调度 一键检查并打印修复建议）。

---

## E. 与之前架构分析的对应

- 路由全貌、鉴权（JWT/PAT）、通知渠道、ORM 表结构、调度/交易日历、OTel、TradingAgents 适配等内部机制，见同目录 `backend-analysis.md`。
- 综合报告（含部署章节 §10.2）见 `report.md` / `report.pdf`。
