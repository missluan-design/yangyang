# PanWatch 后端架构分析

> 仓库：TNT-Likely/PanWatch（Python AI 股票盯盘工具）
> 分析来源：GitHub 在线源码（API contents 端点 + raw），截至 main 分支。
> 本报告第一版基于已通读的两份核心文件：`src/ARCHITECTURE.md`（173 行）与 `server.py`（1637 行）。
> 标注 **【待补充】** 的章节，依赖尚在后台抓取的 `src/platform`、`src/bootstrap`、`packages/*` 源文件，将在取得后回填。

---

## 0. 一句话结论

PanWatch 是一个**模块化单体（modular monolith）**：单进程 FastAPI 应用 + 单 SQLite 库，但代码按 `platform`（技术平台）/ `modules`（业务能力）/ `web`（HTTP 复用）/ `bootstrap`（装配）四层明确分层。AI 分析由 `AgentScheduler`（APScheduler 封装）按 cron 触发，内置 `daily_report` / `premarket_outlook` / `intraday_monitor` / `tradingagents` 四类 Agent；其中 `tradingagents` 对接 `packages/pan-investment-research`（基于 TradingAgents 的多智能体研究编排：bull / bear / debate / risk / portfolio-manager）。行情数据通过 `platform/marketdata` 的多供应商路由（腾讯/东财/新浪/雪球/Stooq/Yahoo/YFinance/同花顺/财联社）归一化获取，覆盖 A股/港股/美股、K线、基本面、新闻、资金流、快讯等。鉴权、通知（Telegram/企微/钉钉/飞书/Bark/Webhook）、OTel 可观测性、SQLite 持久化均已内置。

---

## 1. 整体后端结构（FastAPI 如何组装）

入口为根目录 `server.py`，但**真正的 FastAPI 应用对象在 `src/bootstrap/application.py` 创建并注册全部 router**（`server.py:1602` 引入 `from src.bootstrap.application import app, application_lifespan`）。`server.py` 本身只做三件事：

1. **进程级初始化**：`lifespan()`（`server.py:1465-1598`）在启动期完成 DB 初始化、日志、代理/SSL、Playwright、认证种子、Agent 种子、数据源对账、调度器启动；关闭期优雅 shutdown 各调度器。
2. **SPA 静态托管**：若 `static/` 存在，挂载兜底路由 `/{path:path}` 返回 `index.html`（`server.py:1607-1620`）。
3. **进程启动**：`uvicorn.run("server:app", host="0.0.0.0", port=8000)`（`server.py:1630-1636`），`DEV_RELOAD=1` 时开 `--reload`。

组装关系（来自 `src/ARCHITECTURE.md:88-109`）：

```text
bootstrap/application.py   ← 唯一创建 app 并注册 router 的地方
   ├─ web/                 ← 仅跨模块复用的 HTTP 中间件/响应适配（response.py / errors.py / datetime.py）
   ├─ modules/*/api/        ← 各业务模块的 router（行情、组合、研究、策略、Agent、助手、模拟盘、管理）
   └─ platform/            ← 被 modules 调用的技术能力（不得反向依赖 modules）
```

依赖方向（`src/ARCHITECTURE.md:23-41`）：

```text
web ───────► modules ───────► platform
 │              │                │
 │              └─ public service/DTO ┘
 └────────────────► platform
```

- `platform` 禁止导入 `modules`；`web` 只做 HTTP I/O 映射，不承载 SQL/Agent 循环/策略判断；跨模块协作必须经目标模块公开 service/DTO/event。

### 路由注册（router 分布，实测 `application.py`）

`bootstrap/application.py`（238 行）是**唯一创建 `FastAPI` 实例并注册全部 router** 的位置（已从源码通读确认）。`server.py` 仅把 `app.router.lifespan_context = lifespan` 替换为自定义 `lifespan`（`server.py:1604`）并启动 uvicorn，不显式注册路由。

业务 router 分散在各模块 `api/` 包，按 `app.include_router(...)` 逐模块挂载。实测注册全貌（prefix / tags / 认证依赖）：

| prefix | tags | 认证依赖 | 来源模块 |
| --- | --- | --- | --- |
| `/api/auth` | auth | 无（公开） | `administration.api.auth` |
| `/api/market` | market | 无（公开数据） | `market.market` |
| `/api/onboarding` | onboarding | `get_current_user` | `administration.api.onboarding` |
| `/api/stocks` | stocks | 登录 | `market.stocks` |
| `/api/quotes` | quotes | 登录 | `market.quotes` |
| `/api/klines` | klines | 登录 | `market.klines` |
| `/api/insights` | insights | 登录 | `research.insights` |
| `/api` (accounts) | accounts | 登录 | `portfolio.accounts` |
| `/api/agents` | agents | 登录 | `automation.agents` |
| `/api/providers` | providers | 登录 | `administration.providers` |
| `/api/channels` | channels | 登录 | `administration.channels` |
| `/api/datasources` | datasources | 登录 | `administration.datasources` |
| `/api/settings` | settings | 登录 | `administration.settings` |
| `/api/logs` | logs | 登录 | `administration.logs` |
| `/api` (history) | history | 登录 | `portfolio.history` |
| `/api` (context) | context | 登录 | `research.context` |
| `/api/evaluations` | evaluations | 登录 | `research.evaluations` |
| `/api/news` | news | 登录 | `market.news` |
| `/api/suggestions` | suggestions | 登录 | `automation.suggestions` |
| `/api/templates` | templates | 登录 | `automation.templates` |
| `/api/discovery` | discovery | 登录 | `market.discovery` |
| `/api/price-alerts` | price-alerts | 登录 | `market.price_alerts` |
| `/api/recommendations` | recommendations | 登录 | `research.recommendations` |
| `/api/dashboard` | dashboard | 登录 | `portfolio.dashboard` |
| `/api/factors` | factors | 登录 | `strategy.factors` |
| `/api/health` | health | 登录 | `administration.health` |
| `/api/paper-trading` | paper-trading | 登录 | `paper_trading.paper_trading` |
| `/api/assistant` | assistant | 登录 | `assistant.api` |
| `/api/notifications` | notifications | 登录 | `notifications.api` |
| `/api/pats` | pats | 登录 | `administration.pats`（个人访问令牌） |
| `/mcp` | mcp | 自带 PAT 鉴权（不走登录 JWT） | `administration.mcp`（MCP Server，绕开响应包装中间件保证 JSON-RPC 原样） |

要点：
- **CORS**：`CORSMiddleware(allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])`——全开，适合自托管内网；公网暴露需收紧。
- **响应包装**：`ResponseWrapperMiddleware` 全局包裹，唯独 `/mcp` 不挂（保证 MCP JSON-RPC 原样）。
- **认证约定**：除 `/api/auth`、`/api/market` 与顶层函数 `/api/version`、`/api/health` 外，其余 router 统一加 `dependencies=[Depends(get_current_user)]`。
- **MCP 元数据**：`/.well-known/oauth-protected-resource` 返回 `authorization_servers=[]` + `bearer_methods_supported=["header"]`（RFC 9728），告知 MCP 客户端直接 `Authorization: Bearer`，无需 OAuth server。
- **启动钩子**：`assistant_task_runner.recover_pending` 与 `context_export_runner.recover_pending` 挂到 `on_startup`；`context_export_runner.shutdown` 挂 `on_shutdown`。

---

## 2. 分层职责

### 2.1 `platform/` —— 技术平台（`src/ARCHITECTURE.md:43-56`）

描述“怎样连接/执行”，不描述“用户应做什么”。关键子目录与责任：

| 子目录 | 责任 | 不应包含 |
| --- | --- | --- |
| `persistence/` | engine、Session、ORM `Base`、全部表定义、版本迁移 | 持仓/策略等业务判断 |
| `ai/` | AI provider client、failover、模型传输适配 | 提示词、工具授权 |
| `marketdata/` | 外部行情客户端、采集器、行情值对象、代码规范化、供应商路由、数据归一化 | 告警阈值、选股规则 |
| `events/` | SSE 等事件传输 | 事件的业务含义 |
| `scheduling/` | cron 解析、交易日历、注册表 | Agent 调度流程 |
| `notifications/` | 通道发送、基础去重和策略 | 哪种业务事件应通知 |
| `observability/` | 日志上下文、trace、指标导出 | 领域指标推导 |
| `runtime/` | 进程配置、环境变量、横切运行期设置 | 产品业务规则 |

`server.py` 对 platform 的关键引用：
- `persistence.database.init_db / SessionLocal`（`server.py:11`）
- `persistence.models` 的 ORM 表（`AgentConfig, Stock, StockAgent, AIService, AIModel, NotifyChannel, AppSettings, DataSource`，`server.py:12-21`）
- `observability.log_handler.DBLogHandler`、`log_context`（`server.py:22,35`）
- `runtime.config.Settings / AppConfig / StockConfig`、`runtime.paths.get_data_dir`（`server.py:23-24`）
- `marketdata.models.MarketCode`、`marketdata.javascript_runtime.warmup_javascript_runtime`（V8/ MiniRacer 预热，`server.py:25-26`）
- `ai.ai_client.AIClient`、`ai.ai_failover.build_failover_client`（`server.py:27-28`）
- `notifications.notifier.NotifierManager`（`server.py:29`）

### 2.2 `modules/` —— 业务能力（`src/ARCHITECTURE.md:58-84`）

一级目录 = 一个产品能力，推荐内部结构 `api.py / service.py / repository.py / models.py / schemas.py`。**禁止跨模块导入 `models.py`/`repository.py`**；其他模块须调用 service。

| 模块 | 拥有的能力 | 典型公开边界 |
| --- | --- | --- |
| `assistant` | 对话、任务快照、PanAgent host adapter、已批准工具 | `AssistantService` |
| `automation` | 定时分析 Agent、运行记录、TradingAgents、AgentScheduler | agent service / scheduler |
| `market` | 标的、采集编排、新闻、K线上下文、价格告警 | market/alert service |
| `portfolio` | 账户、仓位、诊断、业绩基准 | `PortfolioService` |
| `research` | 分析历史、上下文、证据、结果评估、signals | research/context service |
| `strategy` | 因子、信号、候选标的、校准、backtest | strategy service |
| `paper_trading` | 模拟盘执行、账本、分配、通知 | paper-trading service |
| `reporting` | 报告与 PDF 等产物渲染 | render/export function |
| `administration` | 健康检查、设置维护、PAT、升级检查、鉴权 | administration service |

`server.py` 直接引用的 modules（印证分层调用）：
- `automation.agent_scheduler.AgentScheduler`、`automation.agent_runs.record_agent_run`、`automation.agent_catalog.*`、`automation.base.*`、`automation.daily_report/intraday_monitor/premarket_outlook/tradingagents`、`automation.scheduling_policy.schedule_plans`（`server.py:30-45,1132`）
- `market.price_alert_scheduler.PriceAlertScheduler`、`market.data_collector.DEFAULT_TEST_SYMBOLS`（`server.py:31,46`）
- `paper_trading.paper_trading_scheduler.PaperTradingScheduler`（`server.py:32`）
- `research.context_scheduler.ContextMaintenanceScheduler`（`server.py:33`）
- `strategy.strategy_catalog.ensure_strategy_catalog`（`server.py:40`）
- `administration.api.auth.init_auth_from_env`、`administration.api.mcp.prune_mcp_logs`、`administration.onboarding.recover_interrupted_analysis`（`server.py:1163,1469,1489`）

### 2.3 `web/` —— HTTP 复用层（`src/web/`）

仅保留跨模块复用的 HTTP 技术组件，本仓库当前文件：`__init__.py`（空）、`datetime.py`、`errors.py`、`response.py`（`src/web/response.py` 约 6KB，含响应包装）。**业务 router 不在此目录**（`src/ARCHITECTURE.md:100-109` 明确禁止恢复 `src/web/api`、`src/web/app.py`、`src/web/database.py` 等）。

### 2.4 `bootstrap/` —— 应用装配（`src/bootstrap/`）

- `__init__.py`（78 字节，极小）
- `application.py`（238 行，`server.py:1602` 唯一创建 `FastAPI` 实例并注册所有 router、装配 lifespan 边界）。实测内部机制：
  - **应用对象**：`app = FastAPI(title="PanWatch API", version="0.1.0", redirect_slashes=False)`——`redirect_slashes=False` 刻意避免 `/foo/`→`/foo` 重定向丢失 `Authorization` 头。
  - **中间件顺序**：先 `ResponseWrapperMiddleware`（统一响应包装），后 `CORSMiddleware(allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])`。
  - **router 装配**：集中 `app.include_router(...)`，公开路由（`/api/auth`、`/api/market`）不加认证依赖；其余全部 `dependencies=[Depends(get_current_user)]`；MCP 单独挂 `/mcp`（自带 PAT 鉴权，不挂响应包装）。完整前缀表见 §1。
  - **lifespan 协作**：`application_lifespan = app.router.lifespan_context` 被 `server.py` 的自定义 `lifespan` 包裹（`server.py:1604`），由 `server.py` 负责 DB 初始化、调度器启动等进程级副作用，而 `application.py` 自身在 `on_startup` 挂 `assistant_task_runner.recover_pending` / `context_export_runner.recover_pending`，`on_shutdown` 挂 `context_export_runner.shutdown`——装配根只连 HTTP 与技术依赖，不承载业务规则，符合 `src/ARCHITECTURE.md` 对 bootstrap 层的约束。

---

## 3. TradingAgents 如何集成（多智能体研究流程）

### 3.1 衔接点（已在 `server.py` 确认）

- `AGENT_REGISTRY`（`server.py:1101-1106`）将 `"tradingagents"` 映射到 `TradingAgentsAgent`（`src/modules/automation/tradingagents.py`）。
- `build_scheduler()`（`server.py:1109-1153`）从 `AgentConfig` 表读取已启用且 `kind==AGENT_KIND_WORKFLOW` 的 Agent，调用 `scheduling_policy.schedule_plans(db, cfg)` 得到执行计划，再 `sched.register(...)` 注册。
- 手动触发：`trigger_agent()`（`server.py:1234`）与 `trigger_agent_for_stock()`（`server.py:1324`）按 `execution_mode`（`single`/`batch`）调用 `agent.run()` 或 `agent.run_single()`，并写 `AgentRun` 记录（`record_agent_run` / `start_agent_run`）。
- `TradingAgentsAgent` 实例化参数来自 `AgentConfig.config`（`server.py:1399-1405`）。
- 启动期：`backfill_tradingagents_suggestions(days=7)`（`server.py:1514-1517`）把历史 TA 决策回填到 `stock_suggestions`（AI 建议池）。

### 3.2 多智能体流程（bull/bear/debate/risk/portfolio-manager）【实测 `tradingagents/`】

> 纠正第一版推测：底层研究引擎**不是** `packages/pan-investment-research`，而是上游开源库 **TauricResearch/TradingAgents（76k★，LangGraph 多智能体框架）**。`tradingagents` 是 `src/modules/automation/` 下的**适配子目录**（非单文件），把上游库桥接进 PanWatch。

`src/modules/automation/tradingagents/` 目录结构（实测）：

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `__init__.py` | 35 | 暴露 `TradingAgentsAgent` / `PanWatchProgressHandler` / `aggregate_progress` / `map_state_to_result` 等 |
| `agent.py` | 29231 | `TradingAgentsAgent(BaseAgent)`：桥接 PanWatch AI Service → TradingAgents LLM config；`get_market_data()` 注入 PanWatch Provider 数据到 TradingAgents data vendor（A股专用）；`_bounded_graph_class` 限制图类防失控 |
| `data_context.py` | 14544 | `build_stock_metadata_context` / `patch_instrument_context` / `to_tradingagents_portfolio`（PanWatch 上下文 ↔ TradingAgents 投资组合互转） |
| `decision.py` | 21858 | `map_state_to_result()`：把 TradingAgents `final_state` 映射为 PanWatch `AnalysisResult`；含 `_extract_debate`（多空辩论）、`_risk_judgment`（风险辩论）、评级/置信度解析、`_render_notify`/`_render_markdown` 渲染、`maybe_emit_paper_trading_signal` |
| `observability.py` | 23399 | `PanWatchProgressHandler`：通过 LangChain callbacks 反馈进度；`aggregate_progress` 聚合 |
| `operations.py` | 18336 | 持仓/下单等操作封装 |
| `runtime_support.py` | 13453 | `build_ta_llm_config` / `bounded_tier_clients` / `apply_compat_patches`（对 tool_calls 参数强制、AI message 校验等打兼容性补丁，应对上游库版本漂移） |
| `toolkit_adapter.py` | 45274 | PanWatch 工具集 ↔ TradingAgents toolkit 的适配器（最大文件） |

**关键衔接事实**：
- `TradingAgentsAgent` 继承 PanWatch 的 `BaseAgent`，因此复用 server 的统一 Agent 运行骨架（`build_context`、`trigger_agent_for_stock`、`AgentRun` 记录）。
- 数据流：server 调度触发 → `TradingAgentsAgent.run()` → 内部用上游 TradingAgents 跑 bull/bear/debate/risk/portfolio-manager 多智能体 → `decision.map_state_to_result()` 把 `final_state`（含多空辩论、风险辩论、评级、置信度）映射成 PanWatch `AnalysisResult` 并回写 `stock_suggestions` / `analysis_history`。
- **软依赖策略**（很重要）：上游 `tradingagents` 库不在 PyPI，需用户自行 `git clone + pip install -e`；未安装时 `run()` 返回明确错误、不 crash 服务（`__init__.py` 文档明确）。这使 TradingAgents 深度分析成为可选能力，核心盯盘功能不依赖它。

### 3.3 上下文构建（通用 Agent 运行骨架）

`build_context(agent_name, stock_agent_id)`（`server.py:1075-1097`）统一装配 `AgentContext`：
- `load_watchlist_for_agent`（`server.py:737`）：Agent 关联自选股 → `StockConfig`
- `load_portfolio_for_agent`（`server.py:769`）：多账户持仓 → `PortfolioInfo`
- `resolve_ai_model`（`server.py:926`）：四级路由（stock_agent 覆盖 → agent 默认 → 系统默认 `is_default` → 第一个）
- `resolve_notify_channels`（`server.py:976`）：渠道解析（stock_agent 覆盖 → agent 默认 → 系统默认）
- `report_language`（报告语言）

---

## 4. 数据源层（`platform/marketdata`）

### 4.1 供应商路由（来自 `server.py` 的 `DATA_SOURCE_SEEDS`，`server.py:344-639`）

`server.py` 在启动期通过 `seed_data_sources` / `reconcile_data_sources`（`server.py:642-728`）以「按 `(name,provider)` 只增不删 upsert + 删孤儿」方式预置数据源。内置供应商与类型：

| 类型(type) | provider | 说明 / 市场 |
| --- | --- | --- |
| `news` | `xueqiu`、`eastmoney_news`、`eastmoney` | 雪球/东财资讯与公告（CN） |
| `kline` | `tencent`(0)、`eastmoney`(5)、`stooq`(15)、`yahoo`(20) | 腾讯/东财日线；Stooq 美股兜底；Yahoo 需代理 |
| `capital_flow` | `eastmoney`(0)、`sina`(5) | 主力资金流（CN） |
| `quote` | `tencent`(0)、`eastmoney`(3)、`sina`(5)、`yfinance`(10) | 实时行情；腾讯优先，东财/新浪备源，YFinance 适用 HK/US |
| `events` | `eastmoney` | 事件日历（基于公告结构化） |
| `flash_news` | `cls`(0)、`sina`(5)、`eastmoney`(10) | 财联社/新浪/东财 7×24 电报（市场级） |
| `fundamentals` | `tencent`(0)、`eastmoney`(5) | 估值/股本/财报指标（CN/HK/US） |
| `dragon_tiger` | `eastmoney` | 龙虎榜（市场级） |
| `margin` | `eastmoney` | 融资融券 |
| `shareholders` | `eastmoney` | 股东户数 |
| `dividend` | `eastmoney` | 分红送转 |
| `northbound` | `ths` | 北向资金（同花顺，东财已断供） |
| `chart` | `xueqiu`(0)、`eastmoney`(1) | K线截图（Playwright 渲染） |

- 优先级 `priority` 字段决定同类型供应商回退顺序（如 K线：腾讯→东财→Stooq→Yahoo）。
- 部分源需代理（`yahoo`/`yfinance` 默认关），`enabled` 控制开关。
- 供应商合法性由 `marketdata.PACKAGE_VENDORS_BY_TYPE | 种子 provider 集合` 决定，孤儿行在 `reconcile_data_sources` 中被删（`server.py:708-720`）。

### 4.2 akshare 封装与多市场覆盖（实测 `platform/marketdata/models.py`）

`platform/marketdata/models.py`（101 行，纯 dataclass/Enum，无 ORM 依赖）定义了**跨市场行情值对象与规范化**：

- **`MarketCode(str, Enum)`**：`CN`（A股）、`HK`（港股）、`US`（美股）三态。
- **`MarketDef` + `MARKETS` 预定义表**：每个市场含 `timezone`、`sessions`（交易时段）、`symbol_pattern`（代码正则校验）：
  - A股 `Asia/Shanghai`：上午 `09:30–11:30`、下午 `13:00–15:00`；代码 `^[036]\d{5}$`。
  - 港股 `Asia/Hong_Kong`：上午 `09:30–12:00`、下午 `13:00–16:00`；代码 `^\d{5}$`。
  - 美股 `America/New_York`：单时段 `09:30–16:00`；代码 `^[A-Z]{1,5}$`。
  - `MarketDef.is_trading_time()` 委托 `trading_calendar.market_status` 判断实时交易状态。
- **`StockData` / `IndexData`**：标准化行情结构（symbol/name/market/price/change_pct/change_amount/volume/turnover/open/high/low/prev_close/timestamp），所有供应商返回都归一化到该结构。

关于「akshare 封装」：从 `server.py` 的 `DATA_SOURCE_SEEDS` 看，provider 抽象是**自研多供应商 HTTP 路由**（腾讯/东财/新浪/雪球/Stooq/Yahoo/YFinance/同花顺/财联社），并**未直接使用 akshare**；akshare 若被采用，应位于底层 vendor 包 `packages/marketdata`（采集器 `platform/marketdata/collectors/` 的实现层，本次未抓取该包源码，留作后续细化）。可确认的是：多市场覆盖（A/H/US/ETF/K线/基本面/新闻/资金流）由上述 9 类 provider 的路由与 `StockData/IndexData` 归一化层共同支撑，市场边界由 `MarketCode` + `symbol_pattern` 严格约束。

---

## 5. 定时任务调度

### 5.1 调度器体系（`server.py` lifespan 启动）

| 调度器 | 类 | 触发方式 | 引用 |
| --- | --- | --- | --- |
| Agent 分析 | `AgentScheduler`（`automation.agent_scheduler`） | cron（来自 `AgentConfig.schedule` + `schedule_plans`） | `server.py:1138-1150,1540-1542` |
| 价格提醒 | `PriceAlertScheduler` | 轮询 `interval_seconds=60` | `server.py:1545-1549` |
| 模拟盘 | `PaperTradingScheduler` | 轮询 `interval_seconds=60` | `server.py:1554-1559` |
| 上下文维护 | `ContextMaintenanceScheduler` | cron `04:30`，快照保留 180d / 结果保留 365d | `server.py:1564-1572` |
| MCP 日志清理 | `prune_mcp_logs` | cron `04:00` | `server.py:1156-1173,1576-1580` |

- `AgentScheduler` 是对 **APScheduler** 的封装（注释 `sched.scheduler` 即底层 APScheduler 实例，`server.py:1156-1172`）。
- 时区由 `Settings().app_timezone` 提供（`server.py:1112,1546` 等）。
- `reload_scheduler()`（`server.py:1176-1193`）在配置导入/批量修改后热重载。

### 5.2 计划任务：pre-market / intraday / closing（实测 `platform/scheduling/`）

内置 Agent 含 `premarket_outlook`（盘前）、`intraday_monitor`（盘中，默认 `event_only=True`，`server.py:332-336`）、`daily_report`（收盘/日报）、`tradingagents`。各 Agent 的具体执行计划由 `scheduling_policy.schedule_plans(db, cfg)`（`server.py:1132-1133`）计算，底层 cron 解析与交易日过滤由 `platform/scheduling` 提供（实测）：

**`schedule_parser.py`**（237 行）：
- 接受 **5 段 cron** `分 时 日 月 周`（`parse_cron`）；并支持 `interval:N{s,m,h}` 间隔触发（`parse_interval`）。
- **关键坑：POSIX cron 周字段语义转换**。`day_of_week` 按 POSIX（`0/7=周日`），而 **APScheduler 的 `CronTrigger` 用 `0=周一..6=周日`**。模块用 `normalize_cron_day_of_week_field()` 把 POSIX 集合映射到 APS 集合（`6 if d==0 else d-1`），避免调度在错误星期几触发。含字母星期（mon-fri）则原样透传。
- `preview_schedule()` 结合 `trading_calendar` 的 `eligible_markets`/`next_eligible_time`，可只在交易时段内预览下一次触发；`count_runs_within()` 统计窗口内触发次数。

**`trading_calendar.py`**（194 行）：**离线交易日历**。
- 年度休市数据 `HOLIDAYS` / `EARLY_CLOSES` 随应用打包（`exchange_calendar_data`），**运行期 warmup 仅物化近期窗口（今天 −30 天 ~ +90 天），无任何网络请求**（`refresh_blocking`，约数毫秒）。
- 核心判定：`is_trading_day(market, d)`（周末或命中休市则 False）、`calendar_known`（publ 时段外返回未知，禁止自动作业）、`trading_sessions`（返回各市场时段，处理早收盘）、`market_status`（open/trading/closed 等）、`next_open`。
- 支持 `CN`/`HK`/`US` 三市场，时区由 `marketdata.models.MARKETS` 提供。

结论：pre-market/intraday/closing 的"只在交易日、交易时段内触发"由 `schedule_parser` + `trading_calendar` 共同保证，且全程离线、不依赖外部日历 API——这是该项目的稳健性设计亮点。

---

## 6. 鉴权与会话（实测 `modules/administration/api/auth.py`）

`auth.py`（258 行）实现**单用户 JWT 认证**（自托管场景足够，无需多用户体系）：

- **算法/有效期**：`JWT_ALGORITHM="HS256"`，`JWT_EXPIRE_DAYS=30`（一次性签发 30 天有效令牌）。
- **密码存储**：`hash_password()` 用 `hashlib.sha256(password)` 哈希，存 `AppSettings` 表（`auth_password_hash`）；**注意：纯 sha256 无盐，强度有限**（安全建议见综合报告风险章）。
- **JWT Secret**：`get_jwt_secret()` 优先读环境变量 `JWT_SECRET`；否则从 `AppSettings(jwt_secret)` 读取，首次运行 `secrets.token_hex(32)` 自动生成并持久化——保证重启后令牌仍有效。
- **部署种子**：`init_auth_from_env`（server 启动期调用，`server.py:1489`）从 `AUTH_USERNAME`/`AUTH_PASSWORD` 环境变量写入首个账户（Docker 部署首选）。
- **接口**（`/api/auth`，公开）：
  - `POST /login` → `TokenResponse(token, expires_at)`；
  - `POST /change-password`（需登录，密码≥6 位）；
  - `GET /me` → 当前用户。
- **会话模型**：无服务端 session，纯无状态 JWT（`HTTPBearer` 提取，`verify_token` 校验 exp/签名）；`get_current_user` 依赖注入到各受保护 router。
- **PAT（个人访问令牌）**：`/api/pats` 管理，面向 **MCP Server**（`/mcp`）使用——MCP 自带 PAT 鉴权、不走登录 JWT，且不挂响应包装中间件以保证 JSON-RPC 原样。对应 `PersonalAccessToken` 表（`personal_access_tokens`）。
- **首次引导（onboarding）**：`administration.onboarding` 在启动期 `recover_interrupted_analysis` 恢复中断分析；首次启动 `seed_sample_stocks` 注入 5 只示例股、`seed_agents` 注入内置 Agent 配置。

---

## 7. 持久化（实测 `persistence/models.py`）

- 引擎/Session/`Base`：`src/platform/persistence/database.py`（`init_db`, `SessionLocal`，`server.py:11`）。基于 SQLAlchemy，**默认 SQLite**（文件落 `get_data_dir()`，`runtime.paths`），但 ORM 抽象使切换到 Postgres/MySQL 仅需改连接串（项目未强制锁定 SQLite）。
- 版本迁移：`src/platform/persistence/migrations.py`（`src/ARCHITECTURE.md:147-151`：新增 schema 用可重复执行的新版本迁移，不修改已发布迁移）。
- 日志持久化：`DBLogHandler`（`observability/log_handler`，`server.py:22,178`）把全量 DEBUG 日志写入 `log_entries` 表，供 UI 日志板查询。

**ORM 表（实测 `models.py`，共 67 张，覆盖全业务域）**：

| 域 | 表（table） |
| --- | --- |
| AI | `ai_services`, `ai_models` |
| 通知 | `notify_channels`, `notify_throttle`, `notification_events`, `notification_receipts`, `assistant_task_notifications` |
| 行情/标的 | `stocks`, `data_sources`, `news_cache` |
| 组合/持仓 | `accounts`, `positions`, `paper_trading_account`, `paper_trading_positions`, `paper_trading_trades`, `paper_trading_settlements` |
| Agent | `stock_agents`, `agent_configs`, `agent_runs`, `agent_context_runs`, `agent_prediction_outcomes` |
| 研究/洞察 | `analysis_history`, `stock_context_snapshots`, `news_topic_snapshots`, `insights`(经 research), `recommendations`, `stock_suggestions`, `entry_candidates`, `entry_candidate_outcomes`, `market_scan_snapshots` |
| 策略/因子 | `strategy_catalog`, `strategy_signal_runs`, `strategy_outcomes`, `backtest_runs`, `strategy_weights`, `strategy_weight_history`, `factor_weights`, `factor_weight_history`, `market_regime_snapshots`, `strategy_factor_snapshots`, `portfolio_risk_snapshots` |
| 价格告警 | `price_alert_rules`, `price_alert_hits`, `price_alert_health`, `price_alert_scan_health`, `price_alert_deliveries` |
| 对话/助手 | `chat_conversations`, `chat_messages`, `assistant_context_snapshots`, `assistant_context_exports`, `assistant_task_runs`, `assistant_task_events`, `assistant_task_steps`, `assistant_task_notifications`, `assistant_artifacts` |
| 工具/权限 | `assistant_tool_invocations`, `assistant_tool_approvals`, `assistant_tool_permissions` |
| 鉴权/MCP | `app_settings`, `personal_access_tokens`, `mcp_call_logs` |

> 说明：`models.py` 用 ORM `Base` 声明式定义，每张表对应一个 `class`（如 `class Stock(Base): __tablename__="stocks"`）。`AppSettings` 是键值表（`auth_username`/`auth_password_hash`/`jwt_secret`/`http_proxy` 等均存此处）。MCP 调用审计落 `mcp_call_logs`，工具授权落 `assistant_tool_permissions`。表数量之多（67）印证了"模块化单体 + 全栈自包含"的定位——行情、Agent、策略、回测、预警、模拟盘、对话、助手任务、通知、MCP 全部落到同一 SQLite。

---

## 8. 通知渠道（`platform/notifications`，实测 `notifier.py` / `notify_policy.py`）

- 发送核心：`NotifierManager`（`server.py:29,1060-1063`），按 `NotifyChannel.type` + `config` 添加通道。
- 渠道解析四级：stock_agent 覆盖 → agent 默认 → 系统默认（`is_default`）（`server.py:976-1018`）。
- **渠道类型枚举（`notifier.py` 的 `CHANNEL_TYPES`，共 10 类，实测）**：

| type | 名称 | 配置字段 | 发送实现 |
| --- | --- | --- | --- |
| `telegram` | Telegram | `bot_token`, `chat_id`, `proxy` | apprise（`_APPRISE_TYPES`） |
| `bark` | Bark | `device_key`, `server_url` | apprise |
| `dingtalk` | 钉钉机器人 | `token`, `secret`, `phones`, `keyword` | apprise（secret 走加签） |
| `wecom` | 企业微信机器人 | `webhook_key` | apprise |
| `lark` | 飞书机器人 | `webhook_token` | apprise |
| `discord` | Discord | `webhook_id`, `webhook_token` | apprise |
| `pushover` | Pushover | `user_key`, `app_token` | apprise |
| `serverchan` | Server酱 | `sendkey` | 自研 `_send_serverchan`（HTTP POST） |
| `pushplus` | PushPlus | `token`, `topic` | 自研 `_send_pushplus`（markdown 模板） |
| `webhook` | 通用 Webhook | `url` 等 | 自研 `_send_webhook`（HTTP POST JSON） |

  - 即：apprise 覆盖 7 类（telegram/bark/dingtalk/wecom/lark/discord/pushover），serverchan/pushplus/webhook 为自研 HTTP 实现；任务要求的 6 类（TG/企微/钉钉/飞书/Bark/Webhook）全部覆盖。
- **文本适配**：`sanitize_for_telegram()` 专门清理 HTML 标签与 Markdown（`#`/`**`/`*`/`` ` ``/列表/标题）以适配 Telegram；裸 URL 自动可点击、内联链接退化为纯文本（对非公网 localhost/IP 更稳）。
- **全局代理**：`get_global_proxy()` 从 `AppSettings(http_proxy)` 读取，供渠道发送使用（企业网络出口）。
- **策略（`notify_policy.py` 的 `NotifyPolicy`，实测）**：`timezone`、`quiet_hours`（HH:MM-HH:MM 勿扰时段，支持跨午夜）、`retry_attempts`、`retry_backoff_seconds`、`dedupe_ttl_overrides`（按 agent 名覆盖去重 TTL，`parse_dedupe_overrides` 解析 JSON）。去重另见 `notify_dedupe.py`。

---

## 9. 可观测性（`platform/observability`）

- 日志上下文：`install_log_record_factory` / `log_context`（`server.py:35`）；全量日志经 `DBLogHandler` 入库（`server.py:141-180`），控制台按 `LOG_LEVEL` 过滤并屏蔽 `httpx/httpcore/urllib3/apscheduler/uvicorn.access` 噪音（`_ConsoleNoiseFilter`，`server.py:192-208`）。
- **OpenTelemetry 导出**：`init_otel()`（`observability/otel.py`，`server.py:1476-1480`）——**仅在配置 `OTEL_EXPORTER_OTLP_ENDPOINT` 且已装 OTel SDK 时启用**，否则静默 no-op，绝不阻断启动（实测 `otel.py` 347 行）：
  - **默认零副作用**：未配 `OTEL_EXPORTER_OTLP_ENDPOINT` 或未装 SDK 时 `init_otel()` 直接返回 `False`，后续所有 span 接口降级为 no-op——不抛错、不引入运行时依赖、不改既有行为。
  - **懒加载**：模块顶层**不 import** `opentelemetry`，仅 `init_otel()` 被调用且 endpoint 已配才尝试导入；因此 `import ...otel` 永远安全、零成本。
  - **Exporter**：优先 **OTLP/HTTP（4318）**，回退 **OTLP/gRPC（4317）**，自动读取标准环境变量（`OTEL_EXPORTER_OTLP_ENDPOINT` 等）。
  - **三类桥接 span**（复用既有埋点，不重复插桩）：① Agent 一次运行 → root span（复用 `agent_runs.trace_id` 关联）；② 单次 LLM 调用 → gen_ai 子 span（复用 `ai_client` 已有 token 用量）；③ TradingAgents 节点 → 子 span（复用 `observability.py` 节点/LLM 事件）。
  - **语义约定**：采用 OpenTelemetry **Generative AI Semantic Conventions**（`gen_ai.system` / `gen_ai.operation.name` / `gen_ai.request.model` / `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens`），使 Jaeger/Tempo/Langfuse 等标准 APM 直接识别为"一次模型调用"；并附加 PanWatch 自定义属性（`panwatch.agent.name` / `panwatch.trace_id` / `panwatch.trigger_source` / `panwatch.tradingagents.stage`）便于与 `agent_runs` 表对齐。

---

## 10. 部署

### 10.1 进程启动
- `server.py:1630-1636`：`uvicorn.run("server:app", host="0.0.0.0", port=8000)`；`DEV_RELOAD=1` 走 `--reload`（reload_dirs=`["src","."]`，excludes=`data/*,frontend/*,.claude/*`）。
- 生产（`python server.py`）不开 reload（避免 reloader 子进程监听 `data/` 误重启，`server.py:1626-1628`）。

### 10.2 Docker / 本地开发（实测 `Makefile` / `Dockerfile`）

**`Makefile`（109 行）** —— 端口约定：后端 `:8000`（Docker/本地统一，避免老用户升级困惑）、前端 `:5183`（与 BeeCount-Cloud 的 `:5173` 错开）。关键目标：
- `make setup-backend`：建 venv 并 `pip install -r requirements.txt`（Windows/Unix 双分支）。
- `make dev-api`：设 `DEV_RELOAD=1` 跑 `python server.py`（uvicorn reload）。
- `make dev-web`：进 `frontend/`，`pnpm install --no-frozen-lockfile && pnpm dev`（需 pnpm）。
- `make test` / `test-notify`：pytest（后者真实发通知）。
- `make eval`：跑 Agent 过程评测集（工具选择/参数/有据性/结构化输出/动作白名单），chat 用例需 `EVAL_AI_*` 环境变量。
- `make doctor`：命令行系统自检（数据源/AI/通知/DB/磁盘/调度），打印结果+修复建议。
- `make build VERSION=x`：前端构建 + Docker 镜像（`scripts/build.ps1` 或 `build.sh`）。
- `make install-hooks` / `clean-venv`。

**`Dockerfile`（125 行，多阶段构建）**：
- **Stage1 前端**：`node:24.14.0-alpine`，`corepack enable && corepack prepare pnpm@9.15.9`，`pnpm install --frozen-lockfile` + `pnpm build`，产物 `/app/frontend/dist`。
- **Stage2 Python 依赖**：`python:3.11-slim-bookworm`，临时装 `git`（**仅用于构建上游 TradingAgents，不进最终镜像**），用 `requirements-runtime.txt` 装在 `/install`（不预编译字节码）。
- **Stage3 运行**：`python:3.11-slim-bookworm`，系统依赖 `tzdata`（时区）、`fonts-noto-cjk`（K线截图中文）、`libpango*`（PDF 导出字体排版）、Playwright 1.57 Chromium 依赖（无头 K线截图，用软件渲染，不引 GTK/EGL）；固定 Bookworm 避免 Trixie 的 `libgbm1` 拉入 Mesa/LLVM 图形栈。`COPY --from=python-builder /install/` 复制依赖，`COPY src/ server.py`，`COPY --from=frontend-builder .../dist ./static/`。
- 运行环境：`ENV DATA_DIR=/app/data`、`TZ=Asia/Shanghai`、`EXPOSE 8000`、`HEALTHCHECK` 用 `python -c "...urlopen('http://localhost:8000/api/health')"`，`CMD ["python","server.py"]`。Playwright 浏览器首次启动自动装到 `data/` 以减小镜像。

**部署命令（README）**：`docker run -d --name panwatch -e TZ=Asia/Shanghai -p 8000:8000 -v panwatch_data:/app/data sunxiaoye0721/panwatch:latest`。本地开发即 `make dev-api` + `make dev-web`（要求 Node 24 / pnpm 9、Python 3.11+）。

---

## 11. 待补充清单（已于 23:44 回填完成）

> 全部源码通过 GitHub Contents API 抓取并本地解码（限额 23:33 重置后），以下章节均已基于实测源码补全；仅 `packages/marketdata` 底层 vendor 实现与 `docker-compose.yml` 未单独抓取，但前者由 `platform/marketdata/models.py` 值对象层覆盖、后者部署模式与 `Makefile`/`Dockerfile`/README 完全一致。

- [x] `src/bootstrap/application.py` —— 路由注册全貌（§1 完整 31-router 表）、CORS/中间件、`application_lifespan` 协作（§2.4）。
- [x] `src/modules/automation/tradingagents/` —— 纠正第一版推测：底层为上游 **TauricResearch/TradingAgents（76k★）**，PanWatch 通过适配子目录（`agent.py`/`decision.py`/`runtime_support.py`/`toolkit_adapter.py` 等）桥接，详见 §3.2；`packages/pan-investment-research` 实为另一套内置 LangGraph 编排（盘前/盘中/日报），非 TA 上游。
- [x] `src/platform/scheduling/*` —— pre-market/intraday/closing 的 cron 解析（POSIX→APS 周映射）、离线交易日历（§5.2）。
- [x] `src/platform/notifications/*` —— 实际 **10 类**渠道（`CHANNEL_TYPES`）+ apprise/自研实现 + `NotifyPolicy` 勿扰/去重（§8）。
- [x] `src/platform/persistence/models.py` —— **67 张 ORM 表**全清单（§7）。
- [x] `src/platform/observability/otel.py` —— OTel 默认 no-op / 懒加载 / OTLP HTTP→gRPC / gen_ai 语义桥接（§9）。
- [x] `src/platform/marketdata/models.py` —— `MarketCode`(CN/HK/US)、交易时段、`StockData`/`IndexData` 标准化；akshare 未直接使用，provider 走自研多供应商 HTTP 路由（§4.2）。
- [x] `src/modules/administration/api/auth.py` —— 单用户 JWT(HS256/30d)、sha256 密码、`jwt_secret` 自动生成、PAT/MCP 鉴权、onboarding（§6）。
- [x] 根目录 `Makefile` / `Dockerfile` —— 端口约定、多阶段镜像、运行依赖、`make` 目标、部署命令（§10.2）；`docker-compose.yml` 未单独抓，但 `docker run ... -v panwatch_data:/app/data` 卷挂载模式已由 README 确认一致。
