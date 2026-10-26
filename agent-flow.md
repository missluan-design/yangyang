# PanWatch Agent 编排与端到端数据流分析报告

> 仓库：`TNT-Likely/PanWatch`（main 分支，分析时点 2026-10-24）
> 数据源：仅通过 GitHub API / raw 网络读取，未 clone；关键文件已落盘核对。
> 范围：Agent 编排、TradingAgents 接入、定时/手动触发、深度分析、助手、通知、可观测性与端到端数据流。

---

## 1. 摘要与核心结论

1. **存在两套相互独立的多智能体系统**，不要混淆：
   - **`pan-investment-research`（自研 LangGraph 包）**：驱动三个「会话型」Agent —— `premarket_outlook`（盘前展望）、`intraday_monitor`（盘中监测）、`daily_report`（收盘复盘）。它们共享同一个强制编译图（specialist → bull/bear debate → manager → strategy → risk → decision → execution）。
   - **`TradingAgents`（上游 TauricResearch v0.7.0 依赖）**：仅用于持仓页「大脑图标」触发的**单标的深度分析**，集成在 `src/modules/automation/tradingagents/`。其流程是 4 类分析师 → 多空辩论 → 研究主管 → 交易员 → 风控 → 组合经理最终决策。
2. **`pan-agent-runtime`（独立内核 `pan_agent`）** 是导航级助手的执行引擎：有界 Agent loop、人工审批、checkpoint、RuntimeEvent 流式事件；它与 `pan-investment-research`、`TradingAgents` 都通过「宿主注入 ModelPort / ToolRegistry / EventSink / policy」解耦，本身不依赖数据库或 FastAPI。
3. **调度/触发统一收敛在 `server.py` + `AgentScheduler`**：定时走 APScheduler（cron/interval），手动走 `trigger_agent_for_stock`（trace_id 形如 `man-...`，自动为 `sch-...`）；两者都先写 `agent_runs` 生命周期记录，`trigger_source` 区分 `schedule`/`manual`/`api`。
4. **产物 → 通知 → 前端** 链路：Agent 结果写入分析历史（research history）/ `suggestion_pool`，并通过 `agent_result()` 挂钩通知策略，经 Telegram/WeCom/DingTalk/Feishu/Bark/Webhook 分发；前端轮询 `log_entries`（event=`ta_progress`）或 `/api/agents/runs/{trace_id}/progress` 展示进度。
5. **可观测性基于 OpenTelemetry（默认关闭，no-op）**：根 span `agent_run`，TradingAgents 每个 LangGraph 节点一个 stage span，每次 LLM 调用一个 `gen_ai` 子 span 并回填 token 用量；`trace_id` 贯穿 `agent_runs` 表、日志与 span，实现 agent_run / span / 节点级进度与成本追踪。

---

## 2. 总体架构定位（来自 `src/ARCHITECTURE.md`）

PanWatch 是**模块化单体**：一个 FastAPI 应用 + 一个共享数据库，但按稳定业务边界组织代码。

```
src/
├── bootstrap/      # 应用启动与依赖装配（唯一创建 FastAPI 的地方）
├── platform/       # 与业务无关的技术能力（不可 import modules）
├── modules/        # 产品业务能力（assistant / automation / market / portfolio / research / strategy / paper_trading / reporting / administration）
└── web/            # 跨模块复用的 HTTP 中间件与响应适配
```

依赖方向：`web → modules → platform`，跨模块协作必须经过「拥有模块公开的 service / DTO / event」，禁止模块间直接 import 对方 `models.py`/`repository.py`/`api/`。

与本报告最相关的两个模块：
- **`automation`**：定时分析 Agent、运行记录、TradingAgents 集成、AgentScheduler。
- **`assistant`**：对话、任务快照、PanAgent host adapter、已批准工具（`AssistantService`）。

---

## 3. 两套多智能体研究系统：关系与接入

### 3.1 `pan-investment-research`（LangGraph，会话 Agent 内核）

- 独立 Python 包，仅依赖 Pydantic + LangGraph；**不 import PanWatch、不连数据库、不泄露凭证、不发通知、不下单**。宿主注入 `model port` + 可选只读 `tools` + `event sink`，返回强类型建议与不可变、带版本的研究产物。
- 四张**编译图**分离全量决策、分组研究、专家工具循环、快速盘中决策；specialist 并发执行，每个 worker 等子图完成才返回；重大分歧可请求定向证据/反驳；风控可返回修订计划；所有反馈共享一个配额。
- 接入点：`src/modules/research/orchestration/entry.py: run_research()`，被三个会话 Agent 的 `SessionResearchAgent.run()` 统一调用（`premarket_outlook` / `intraday_monitor` / `daily_report` 的 `collect()` 只负责采集，`run()` 委托给同一张强制图）。

### 3.2 `TradingAgents`（上游 v0.7.0，深度分析内核）

- 由 `src/modules/automation/tradingagents/` 集成：
  - `agent.py`：`TradingAgentsAgent(BaseAgent)`，重写 `collect()`（走 Provider Orchestrator，4 类数据并发拉取）与 `analyze()`（不调单次 `ai_client.chat`，而调 `TradingAgentsGraph`）。
  - `data_context.py`：把 PanWatch 持仓/元数据转为上游 portfolio 输入。
  - `toolkit_adapter.py`：monkeypatch `route_to_vendor`，让上游拿到 A 股数据。
  - `decision.py`：`final_state` → PanWatch `AnalysisResult` 评级映射 + 可选模拟盘信号桥接。
  - `observability.py`：LangChain `BaseCallbackHandler` 进度/用量回调。
- 文档锁定官方 **v0.7.0**；新版以 `final_rating` 为权威评级，`investment_plan` 为研究主管结论。

### 3.3 衔接：`server.py` ↔ `automation` ↔ 两个内核

```
server.py (FastAPI)
 ├─ AGENT_REGISTRY = {"tradingagents": TradingAgentsAgent, ...}
 ├─ build_scheduler()  ──注册──> AgentScheduler(APScheduler)
 │                              └─ _run_agent() → agent.run(context)
 ├─ trigger_agent_for_stock()  (手动/API 触发，trace_id="man-...")
 └─ record_agent_run() ─> agent_runs 表 + agent_result() 挂钩通知
```

三会话 Agent 与 `tradingagents` 都注册为 `BaseAgent` 子类，统一走 `collect()/run()` 契约；差异在于 `run()` 内部委托给 LangGraph（会话 Agent）还是 `TradingAgentsGraph`（深度分析）。前端只在持仓页大脑图标触发 `tradingagents`，在「计划任务」页配置三个会话 Agent 的 cron/interval。

---

## 4. 四种分析师角色（Agent）

| Agent（`name`） | 显示名 | 用途 | 触发方式 | 背后内核 |
| --- | --- | --- | --- | --- |
| `premarket_outlook` | 盘前分析 | 隔夜行情/新闻/技术结构 → 当日交易计划 | 定时（开盘前）/手动 | `pan-investment-research`（模板 `premarket_balanced`） |
| `intraday_monitor` | 盘中监测 | 交易时段异动/技术信号实时监控 | 定时（盘中，需开市）/手动 | `pan-investment-research`（模板 `intraday_fast`） |
| `daily_report` | 收盘复盘 | 收盘后大盘概览+个股分析+次日关注 | 定时（收盘后）/手动 | `pan-investment-research`（模板 `postmarket_deep`） |
| `tradingagents` | TradingAgents 深度分析 | 单标的多 Agent 投资决策（4 分析师→辩论→风控→PM） | **仅手动**（持仓页大脑图标） | 上游 `TradingAgents v0.7.0` |

> 说明：任务书称「四种分析师角色：pre-market outlook、intraday monitor、daily report；以及 TradingAgents 的 bull/bear/debate/risk/portfolio-manager 流程」。代码层面，前三个是**会话 Agent 角色**，第四个 `tradingagents` 是**深度分析 Agent 角色**；而 bull/bear/debate/risk/portfolio-manager 既是上游 TradingAgents 的内部节点，也是 `pan-investment-research` 内部 research-group 图的角色（见 §5、§6）。

会话模板（来自 `pan-investment-research` README，节选）：

| 宿主模板 | Specialist | Bull/bear | 默认 attempts/deadline | 其他默认 |
| --- | --- | --- | --- | --- |
| `postmarket_deep` | technical/flow/events | 实质事件或专家请求时 | 18 / 180s | 全图，有界反馈 |
| `premarket_balanced` | technical/flow/events | 实质事件或专家请求时 | 14 / 90s | 全图，4 tools，3072 out tokens |
| `intraday_fast` | 仅触发的技术/事件 | 省略 | 6 / 45s | 2 tools，并发 2，无反馈，1536 out tokens |
| `custom_research` | 所选 specialist | 关/仅实质/总是 | 会话默认 | 保留全图用于对比 |

---

## 5. TradingAgents 内部编排：bull / bear / debate / risk / portfolio-manager

集成于 `src/modules/automation/tradingagents/`。阶段顺序（`observability.STAGES_ORDER`）与节点别名如下：

```
data_collection
  └─ 4 类分析师（并行/分别）
        market_analyst        (market_report)
        social_analyst        (social_report，上游名 sentiment_analyst)
        news_analyst          (news_report)
        fundamentals_analyst  (fundamentals_report)
  └─ bull_bear_debate
        bull_researcher  ─┐ 多空辩论（轮数可配，debate_rounds，默认 1）
        bear_researcher  ─┘
  └─ research_manager        (ruling + accepted claim IDs)
  └─ trader                 (trader_investment_plan)
  └─ risk_judge             (aggressive / conservative / neutral 三视角 → risk_judge_decision)
  └─ final_decision         (portfolio_manager → final_trade_decision + final_rating)
```

- 节点阶段映射集中在 `NODE_STAGE_ALIASES`（上游 LangGraph 0.5/0.7 节点改名只需改这一张表）。
- 评审计入 `final_state`：`market_report/social_report/news_report/fundamentals_report`、`investment_debate_state`、`trader_investment_plan`、`risk_judge_decision`、`final_trade_decision`、`final_rating`、`processed_signal`（BUY/HOLD/SELL）。
- **评级映射**（`decision.py`）：上游 5 档 → PanWatch 显示标签/动作：
  `buy→买入/买`、`overweight→增持/加`、`hold→持有/持`、`underweight→减持/减`、`sell→卖出/卖`；无法解析 PM 评级时返回 `REVIEW → 待人工复核`（**不是可交易的 Hold**，不进交易与命中率统计）。
- 模拟盘联动默认关闭（`emit_paper_trading_signal=False`）；开启后仅符合条件的买入/卖出建议写入 `StrategySignalRun` 驱动模拟盘。

---

## 6. `pan-investment-research` 内部编排（会话 Agent 背后）

```
Investment run graph（全量）
  dispatch (按 max_group_size 分区标的)
   └─ research（每组并发 await 子图）→ strategy（一份覆盖全部标的的计划）
        → risk（精确计划 + 引用证据）
             ├─ pass        → decision（保留已批准计划字段）→ execution（确定性现金/报价/订单校验）→ 完成（ready/review）
             ├─ revise      → 回到 strategy（消耗共享反馈配额）
             ├─ evidence_needed → 仅重做受影响专家/主管
             └─ reject/unsupported → Review: risk_rejected

Research group graph（单组）
  dispatch (启用 specialist) → technical/flow/events（各自调 expert 子图，并发）
   → positions{辩论是否启用且触发？}
        ├─ 是 → bull（看多） + bear（看空）并发 → manager（裁决 + 接受 claim ID）
        └─ 否 → manager
   manager: ready/pass → 返回组产物；evidence_needed/rebuttal → patch（配额检查）

Expert graph（有界只读工具循环）
  角色输入 → model(role prompt+JSON schema) → tools(allowlist+标的范围+配额)
   → 证据校验(身份/截止) → 在轮次/尝试上限内回到 model → report(校验 schema/标的/引用)
   → 无效且仍有修复额度 → 一次 JSON 修复 → 仍无效 → 校验失败

Intraday fast graph（盘中快速）
  捕获快照 + 类型化 change signals → gate(确定性信号 + 启用域检查)
   ├─ 无信号 → skipped（0 次模型调用，保留上次正式建议）
   └─ 仅变化标的 → 按专家分组（technical/events）→ strategy(紧凑条件组合决策)
        → rules(覆盖/引用/持仓/订单约束) → 完成（仅发布已校验建议）
```

要点：
- 共享预算覆盖所有组、工具、模型 fallback、修复与反馈；所有图可经事件 port 发进度。
- `completion=skipped` 表示无启用变更触发快速研究：不写正式建议/预测/通知，沿用旧建议。
- `completion=complete` 区分「图跑完」与超时/预算耗尽/校验失败/风控拒绝；跑完的图仍可能需人工复核。未完成图的草稿不得进入正式建议或命中率统计。
- 模型角色**看不到账户现金/购买力/原始持仓记录**，仅「持仓存在」进入提案模型；研究先于强制 `execution` 规则节点应用现金、报价时效、可卖数量与复核限制。

---

## 7. 定时 Agent vs 按需/手动触发 Agent

### 7.1 配置与注册（`server.py` + `agent_scheduler.py`）

- `AgentScheduler` 用 APScheduler 的 `AsyncIOScheduler`；`register(agent, schedule, execution_mode, stock_keys, stock_agent_id)`。
- `schedule` 支持：
  - **cron**：5 段 `分 时 日 月 周`（day_of_week 按 POSIX cron 1–5=周一至周五，内部归一化）。
  - **interval**：`interval:3m` / `interval:30s`。
- `execution_mode`：`batch`（一次跑全部 watchlist）或 `single`（逐只 `run_single`，单只失败时继续，不污染其他标的）。
- 上下文**每次执行动态构建**（`context_builder`），拿最新配置。

### 7.2 定时触发的门禁

- **交易日过滤**：`market_allowed(agent_name, market)` 先按交易所日历过滤；不满足则直接跳过，**不创建模型客户端、不采集、不写失败 run**。
- **盘中需开市**：`intraday_monitor` 在 `collect()` 内还做交易时段门禁（`is_market_trading`），非交易时段返回 `skipped`。
- 自动运行在采集/分析前过滤节假日；定时/执行预览共享日历过滤器。
- `reload_scheduler()` 支持热更新计划，无需重启。

### 7.3 手动/按需触发

- 前端持仓页「大脑图标」→ 调 `trigger_agent_for_stock()`，生成 `trace_id="man-{agent}-{symbol}-{ms}"`，并**幂等**写 `agent_runs`（running）。
- `AgentScheduler.trigger_now(agent_name)` 也可立即执行。
- 手动入口同样经过 `start_agent_run`/`record_agent_run` 生命周期，与定时共享同一套记录与通知逻辑。

### 7.4 区别小结

| 维度 | 定时（Scheduled） | 手动/按需（Manual/API） |
| --- | --- | --- |
| 入口 | APScheduler cron/interval | 持仓页大脑图标 / `trigger_agent_for_stock` / `trigger_now` |
| trace_id 前缀 | `sch-...` | `man-...` |
| 触发前过滤 | 交易日 + 开市门禁（自动跳过，无失败 run） | 盘中监测可 `bypass_market_hours`（仅手动分析场景） |
| 配置位置 | 「计划任务」页 / `AgentConfig.config` | 持仓页即时触发 |
| 通知 | 按 `notify_channel_ids` 自动分发 | 同样分发，可 `suppress_notify` 抑制 |

---

## 8. 深度分析流程与产物

### 8.1 流程（TradingAgents 深度分析）

```
持仓页大脑图标（单标的）
  → server.trigger_agent_for_stock(agent_name="tradingagents")
  → start_agent_run(trace_id, trigger_source="manual")
  → context 注入 _trace_id / _force_refresh
  → TradingAgentsAgent.run(context)
        collect(): Provider Orchestrator 4 类数据并发拉取（行情/社媒/新闻/基本面）
        analyze(): 调 TradingAgentsGraph（见 §5）
            ├─ PanWatchProgressHandler 注入 callbacks（节点/LLM/工具生命周期 + 用量）
            ├─ 同标的同日缓存 get_today_cache_key（跳过重复 LLM 调用）
            └─ 硬超时 timeout_minutes（默认 30min），采集单源 45s 超时
  → map_state_to_result(): final_state → AnalysisResult
  → save_analysis() 写入分析历史（与上游决策日志分开保存）
  → record_agent_run(status=success/failed, notify_*…)
```

### 8.2 产物

- **评级建议**：`final_rating` → 买入/增持/持有/减持/卖出，或 `REVIEW（待人工复核）`。
- **研究主管结论**：来自 `investment_plan`。
- **多份报告文本**：4 分析师报告、辩论历史、交易员意见、风控判定、最终决策书。
- **图表/建议**：由 PanWatch `reporting` 模块渲染（PDF/导出）；`suggestion_pool` 记录建议池。
- **成本**：`cost_usd` 与供应商真实 token 用量（`pan-agent-token-meter.normalize_provider_usage`）在进度回调中累计。
- **持久化**：分析历史与建议池用于展示；TradingAgents 自身决策日志单独保存，按决策日后首个收盘价计入持有期，结束后把相对基准表现与反思带入后续同标的分析。
- **模拟盘联动**（可选）：仅符合条件的买/卖建议写入模拟盘信号。

### 8.3 会话 Agent 的深度分析产物

三会话 Agent 经 `pan-investment-research` 产出：条件组合决策、每个标的 `outlook`(bullish/neutral/bearish)、0–1 置信度、`action`、`execution_status/constraints`；未被校验通过的策略视图保持草稿或空建议列表，绝不臆造「待观察」建议。

---

## 9. 助手（Assistant）能力

来源：`docs/assistant-capabilities.zh-CN.md` + `src/ARCHITECTURE.md` + `packages/pan-agent-runtime`。

**关键流程**（ARCHITECTURE）：
```
/assistant 页面
  → /api/assistant router → AssistantService
  → AgentRuntime (packages/pan-agent-runtime, import 名 pan_agent)
  → ModelPort + 已批准 ToolRegistry
  → runtime events → task persistence + SSE → UI
```

**能力边界（文档强调）**：
- **聊天与上下文**：对话、任务快照、上下文工程（`ContextBudget`/`ContextEngine` 自动压缩，软/硬阈值，最近消息窗口，摘要目标/约束/决定/事实/状态）；`check_watch_request` 从原始请求抽取标的/范围/期限/条件/渠道，并列出能力缺口——它是一个**有边界的解析器**，不保证理解任意自然语言。
- **只读工具与数据时效**：行情工具分离「来源时间」与「获取时间」，标注供应商、可核验来源时间、报价交易日、市场状态与时效；休市/午休/延迟报价**不能**冒充实时收盘价。历史研究/混合持仓估值不因本地存储被当作新数据。
- **价格提醒**：支持价格/涨跌幅/成交额/成交量/量比的平铺 AND/OR，及到期、启用渠道、交易时段、冷却、每日上限、一次/重复模式；审批展示拟保存参数，不把空解析当已支持。
- **持仓诊断**：用正式注册的只读工具 + 宿主权限策略，保存内部调用参数/字段/来源时间/失败/步骤 ID，结果卡把诊断判断关联到依据；内部读取上限 16 次，受任务工具调用上限约束；宿主工具截止 120s，整任务 180s。
- **权限（Human-in-the-loop）**：`pan-agent-runtime` 默认 `ReadOnlyToolPolicy` 只暴露无确认读工具；写入/外部/删除类需宿主 `ToolPolicy` 返回 `ask` → `approval_required` + `AgentCheckpoint` 暂停；用户决定后 `resume` 从 checkpoint 恢复（可在新 runtime 实例/进程，只要宿主已持久化 checkpoint）。
- **通知**：助手通过只读 `get_monitoring_health` 查询价格提醒的扫描心跳、最近检查/成功时间、连续失败、停用/到期/日额度、分渠道投递状态。
- **可移植事件**：插件（如 `pan-agent-tool-research` 渐进式工具发现，`shadow`/`active` 模式）只发 `extension_event`，不扩大权限边界；失败回退到当前 Direct 工具集合。

---

## 10. 通知渠道与 Agent 结果联动

### 10.1 渠道

README 明确支持 **Telegram、WeCom（企业微信）、DingTalk（钉钉）、Feishu（飞书）、Bark、Webhook**。

### 10.2 联动机制（`server.py`）

- `resolve_notify_channels(agent_name, stock_agent_id)`：优先取 `StockAgent.notify_channel_ids`，否则取 `Agent.notify_channel_ids`；再读应用设置 `notify_quiet_hours`、`notify_retry_attempts`、`notify_retry_backoff_seconds`、`notify_dedupe_ttl_overrides`，构造 `NotifyPolicy`（`platform/notifications/notify_policy.py`）。
- `_build_notifier(channels)`：依据渠道 ID 构造通知器。
- **结果挂钩**：`record_agent_run()` 在 `status in (success, failed)` 时调用 `src.modules.notifications.sources.agent_result(db, run)`，把 Agent 运行结果接入通知策略 → 按渠道分发。
- Agent 返回 `raw_data` 含 `notified` / `notify_error` / `notify_skipped`，用于 `record_agent_run` 的 `notify_attempted` / `notify_sent` 字段（供 UI 与 `agent_runs` 表展示）。

### 10.3 投递语义（来自 assistant 能力文档，价格提醒 worker 模式，可作通用参考）

- 独立 worker 每 **10s** 检查队列，发送租约 **90s**，单次超时 **45s**，失败按 **30s 指数退避**最多 **5 次**，耗尽可在命中历史确认重试。
- 新命中、站内事件、每目标渠道待发记录在**同一事务**提交；成功渠道不因失败渠道重发；事件编号在重试中保持一致（至少一次投递语义）。
- 渠道被删除/停用时记为「渠道不可用」；无启用默认渠道时保留站内事件并提示配置渠道。
- 静默时段（quiet hours）、去重 TTL、每日触发额度由 `NotifyPolicy` 控制；日额度计触发次数，0 表示不限。

---

## 11. 可观测性：OpenTelemetry / agent_run / span / 节点级进度与成本

### 11.1 启用条件

`server.py` 生命周期中 `init_otel()`：**仅当配置了 `OTEL_EXPORTER_OTLP_ENDPOINT` 且安装了 OpenTelemetry SDK 才启用**，否则静默 no-op，绝不阻断启动。

### 11.2 trace_id 贯穿

- 每次运行生成 `trace_id`（`sch-...`/`man-...`），同时作为 `run_id` 注入 `log_context`；`agent_runs` 表 `trace_id` 字段（≤64 字符）用于 UI 查询与跨模块幂等（如 `find_active_tradingagents_trace` 防止重复触发）。
- `pan-agent-token-meter` 的 `normalize_provider_usage` 把不同供应商用量归一为 `ModelUsage`（provider 真实用量经 `model_usage` 事件上报，不反向改写已完成的上下文压缩决策）。

### 11.3 span 结构（`tradingagents/observability.py`）

```
otel.agent_run_span(agent_name, trace_id, trigger_source)   # 根 span（调度/手动统一）
 ├─ 每个 LangGraph 节点: otel.start_detached_span("tradingagents.stage <stage>")
 │     属性 ATTR_TA_STAGE, ATTR_AGENT_NAME   （仅保留一个当前阶段 span，避免树膨胀）
 └─ 每次 LLM 调用: otel.start_detached_span("chat <model>")
       属性 GEN_AI_SYSTEM=tradingagents, GEN_AI_OPERATION_NAME=chat, GEN_AI_REQUEST_MODEL
       on_llm_end 回填 GEN_AI_USAGE_INPUT_TOKENS / OUTPUT_TOKENS 后 end_span
```

- 回调基于 LangChain `BaseCallbackHandler`，由 `agent.py` 注入 `Propagator.get_graph_args(callbacks=...)`，**不依赖 debug 文本解析**。
- 进度同时写入 `log_context`，前端轮询 `/api/agents/runs/{trace_id}/progress` 经 `aggregate_progress()` 聚合出：`current_stage`、`completed_stages`、`elapsed_sec`、`total_cost_usd`、`token_usage`、`active_operation`、各阶段 `stages[]`、`data_sources[]`。

### 11.4 阶段映射（文字版节点进度树）

```
agent_run (root)
├─ tradingagents.stage data_collection
├─ tradingagents.stage market_analyst
├─ tradingagents.stage social_analyst
├─ tradingagents.stage news_analyst
├─ tradingagents.stage fundamentals_analyst
├─ tradingagents.stage bull_bear_debate
├─ tradingagents.stage research_manager
├─ tradingagents.stage trader
├─ tradingagents.stage risk_judge
└─ tradingagents.stage final_decision
   └─ 每个节点内: chat <model> (gen_ai) spans，含 input/output tokens
```

---

## 12. 端到端数据流图（文字版）

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 用户持仓 / 自选 (portfolio + watchlist)                                        │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                 │ 1. 计划任务(定时器) 或 手动(大脑图标/trigger API)
                                 ▼
            ┌────────────────────────────────────────────────────┐
            │ AgentScheduler._run_agent / trigger_agent_for_stock │
            │ 生成 trace_id (sch-/man-)；start_agent_run(running)  │
            │ 交易日+开市门禁 (market_allowed / is_market_trading) │
            └───────────────────────────┬────────────────────────┘
                                         │
                         ┌───────────────┴───────────────┐
                         ▼                               ▼
          【三会话 Agent】                    【tradingagents 深度分析】
   premarket/intraday/daily_report          TradingAgentsAgent.run
                         │                               │
                         ▼                               ▼
            2. collect(): SignalPackBuilder         collect(): Provider Orchestrator
               + ContextBuilder（行情/技术/           (行情/社媒/新闻/基本面 4 类并发)
               资金流/新闻/事件 + 上下文快照)          │
                         │                               ▼
                         ▼                        analyze(): TradingAgentsGraph
             3. run_research() → 强制 LangGraph        (4 analyst → bull/bear debate →
                编译图 (pan-investment-research)        manager → trader → risk → PM)
                         │                               │ PanWatchProgressHandler
                         │                               │ (节点/LLM/工具回调 + 用量)
                         ▼                               ▼
            4. 策略/风控/决策/执行规则节点        map_state_to_result(): 评级映射
               产出条件组合决策 + outlook/置信度   (final_rating → 买/增/持/减/卖/复核)
                         │                               │
                         └───────────────┬───────────────┘
                                         ▼
            5. 产物落库：分析历史(save_analysis) / suggestion_pool / 报告(reporting 渲染)
                         │
                         ▼
            6. record_agent_run(status, notify_*, model_label, cost)
                         │
                         ├─► agent_runs 表（trace_id 关联，UI 查询）
                         └─► notifications.sources.agent_result(db, run)
                                         │
                                         ▼
            7. NotifyPolicy（quiet hours / dedupe / retry / 渠道）
                         │
                         ▼
            ┌────────────────────────────────────────────────────────┐
            │ 通知渠道：Telegram / WeCom / DingTalk / Feishu / Bark /   │
            │           Webhook（worker 10s 轮询，90s 租约，至少一次）  │
            └────────────────────────────────────────────────────────┘
                         │
                         ▼
            8. 前端展示：分析历史/建议池卡片 + 进度轮询
               (/api/agents/runs/{trace_id}/progress → aggregate_progress
                读取 log_entries event=ta_progress：阶段/成本/token)

   ── 可观测性贯穿全程 ──
   OTel: agent_run(root) → tradingagents.stage*(节点) → chat*(LLM, 含 tokens)
   trace_id 同时写入 log_context + agent_runs + OTel context（默认 no-op，配 OTLP 才导出）
```

---

## 13. 待补充与不确定点（受 GitHub API 限流影响）

- **`src/modules/notifications/sources/agent_result.py`**：因 API 限流未能取回，本文第 10.2/10.3 节基于 `server.py` 的 `resolve_notify_channels`、README 渠道清单与 assistant 能力文档推断；建议补读该文件确认 `agent_result` 对 success/failed 的具体分发分支与各渠道实现位置（`platform/notifications/`）。
- **`agent_catalog.py` / `scheduling_policy.py`**：未能取回；第 7 节的 cron/interval 解析与 `market_allowed`/`scoped_context` 细节基于 `agent_scheduler.py` 中的调用与 ARCHITECTURE 推断，建议补读以确认节假日日历实现与模板默认值来源。
- **`docs/diagrams/tradingagents-flow.svg`（及 .en.svg）**：未能取回（限流）。文字版阶段树已覆盖其核心信息，图形可用于前端展示核对。
- **前端（`frontend/`）**：未读取 SPA 路由/组件，端到端图第 8 步的 UI 细节（如进度组件、通知中心）为基于 API 路径的合理推断。
- **`pan-investment-research` 的 `src/` 实现细节**（engine.py/fast.py/prompts.py/budget.py/ports.py）：仅基于其 README 的架构说明与 mermaid，未读取源码；如需精确到节点/函数级需补读。

---

*报告完。所有结论均来自仓库 main 分支公开文件；网络抓取受 GitHub 匿名速率限制，少量文件见第 13 节标注。*
