# PanWatch 前端架构分析报告

> 仓库：`TNT-Likely/PanWatch` 的 `frontend/` 目录
> 分析方式：纯网络读取（GitHub API 目录树 + 内容抓取，未 clone 仓库）
> 数据来源：已获取 `frontend/` 下 **277 个文件**的完整目录树，并逐字阅读了 `package.json`、入口、`App.tsx`、`page-loaders.ts`、三个内部包 `package.json` 与关键源码（`api/client.ts`、`api/sse.ts`、`api/chat.ts`、`api/auth.ts`、`base-ui/datetime.ts`、`i18n/*`、`public/*`、`hooks/*`、`notifications/NotificationProvider.tsx`、`UI_GUIDELINES.md`）以及 Dashboard/Stocks/Agents/Settings 四个大页的结构信号。
> 版本：**完整版**

---

## 0. 一句话结论

PanWatch 前端是一个 **React 18 + TypeScript + Vite 5 + Tailwind 3 + 自研 shadcn 风格组件库** 的 pnpm monorepo，采用 **「三层内部包（api / base-ui / biz-ui）+ 路由懒加载 + SSE 流式 + 双语 i18n + PWA」** 架构；状态管理以 **React Context + 自定义 hooks** 为主（无 Redux/Zustand），实时性由 **`useQuoteRefresh` 轮询 + `subscribeSSE` 自动重连订阅** 共同支撑，助手对话通过 **自建 SSE 客户端（带 Last-Event-ID 续推与断线重连）** 实现打字机式流式输出。

---

## 1. 技术栈（已核实）

构建工具链文件：`vite.config.ts`、`vitest.config.ts`、`tsconfig.json`、`postcss.config.js`、`tailwind.config.js`、`pnpm-workspace.yaml`。

| 类别 | 技术 | 证据 |
| --- | --- | --- |
| 框架/语言 | **React 18.3** + **TypeScript 5.3** | `package.json` dependencies |
| 构建 | **Vite 5**（dev/build/preview）+ PostCSS + Tailwind 3.4 | `package.json` devDependencies |
| 样式 | **Tailwind CSS 3.4** + `tailwindcss-animate` + `@tailwindcss/typography` | `tailwind.config.js`、`package.json` |
| 组件体系 | **shadcn/ui 风格**：基于 Radix primitives + `class-variance-authority` + `clsx` + `tailwind-merge` | `package.json` 含 `@radix-ui/*`、`class-variance-authority`、`clsx`、`tailwind-merge`；`base-ui/components/ui/*` |
| 图标 | `lucide-react` | `package.json`、`App.tsx` 大量图标导入 |
| 路由 | **react-router-dom 6** | `package.json`、`App.tsx` 的 `Routes/Route/NavLink` |
| 国际化 | **i18next 26 + react-i18next 17**，按需加载命名空间 | `src/i18n/*`、`package.json` |
| 图表 | **Lightweight Charts**（CDN 引入，含 jsDelivr 兜底） | `index.html` 的 `<script defer src="unpkg.../lightweight-charts...">` |
| Markdown | `react-markdown` + `remark-gfm` | `package.json`（助手回答渲染） |
| 日期/时区 | `Intl.*`（原生，无 dayjs/moment；`date-fn` 仅出现在依赖列表疑为占位/未实际使用） | `base-ui/datetime.ts`、`i18n/format.ts` 全用 `Intl` |
| 测试 | **Vitest 3** + `@testing-library/react` + `jsdom` | `package.json` devDependencies；`*.test.ts(x)` |
| 包管理 | **pnpm 9.15.9** workspace（monorepo） | `package.json` `packageManager`、`pnpm-workspace.yaml` |
| PWA | 原生 **Service Worker + Web App Manifest**（非 Workbox） | `public/sw.js`、`public/manifest*.json`、`index.html` |
| 状态管理 | **React Context + hooks**（无第三方全局库） | 全部 Provider 与 `useXxx` hooks，未见 redux/zustand 依赖 |

**关键脚本（`package.json`）：**
- `dev`：`vite`
- `build`：`node scripts/check-ui-primitives.mjs && node scripts/check-timezones.mjs && tsc -b && vite build`（**构建前强制静态校验 UI 原语与时区用法**）
- `test`：`vitest`
- `check:i18n` / `check:ui` / `check:market-colors` / `check:timezones`：对应四个守护脚本
- Node 引擎要求 **24.14.0**、pnpm **9.15.9**（见 `engines`）。

---

## 2. 路由结构与页面职责

入口链路：`src/main.tsx`（挂载 + 全局 Provider）→ `src/App.tsx`（布局、导航、路由表、鉴权守卫）→ `src/router/page-loaders.ts`（路由→页面懒加载映射）。

### 2.1 路由级懒加载（核心机制）
`page-loaders.ts` 通过 `React.lazy(() => import('@/pages/X'))` 为每个路由生成懒加载 chunk，并导出：
- `pageLoaders`：key→`() => import(...)` 的映射（共 14 个路由）。
- `routePages`：各 `LazyExoticComponent`。
- `resolveRouteKey(pathname)` / `preloadRoute(pathname)`：根据当前 URL **预加载即将访问的页面**。

`App.tsx` 在桌面导航 `<NavLink>` 的 `onMouseEnter`/`onFocus` 上调用 `preloadRoute(to)`，实现 **hover/focus 即预取**，显著减少页面切换白屏；登录页 `/login` 单独渲染、不套导航。

### 2.2 路由表（`App.tsx` 的 `<Routes>`）

| 路径 | 页面组件 | 文件 | 规模 |
| --- | --- | --- | --- |
| `/login` | `LoginPage` | `Login.tsx` (7.1K) | 认证入口（setup/login） |
| `/` | `DashboardPage` | `Dashboard.tsx` (38.7K/843 行) | 总览：组合表现、市场状态、发现面板 |
| `/getting-started` | `GettingStartedPage` | `GettingStarted.tsx` (4.4K) | 新用户引导 |
| `/portfolio` | `StocksPage` | `Stocks.tsx` (**137K/2607 行，最大**) | 自选股、行情、K 线、深度分析、价格提醒、持仓 |
| `/opportunities` | `OpportunitiesPage` | `Opportunities.tsx` (40.7K) | 选股机会/信号 |
| `/paper-trading` | `PaperTradingPage` | `PaperTrading.tsx` (39.9K) | 模拟交易 |
| `/assistant` `/assistant/:conversationId` | `AssistantPage` | `Assistant.tsx` (2.0K，薄壳) | AI 助手（重逻辑在 `ChatWidget`/`useAssistantTask`） |
| `/alerts` | `PriceAlertsPage` | `PriceAlerts.tsx` (21.9K) | 价格提醒管理 |
| `/agents` | `AgentsPage` | `Agents.tsx` (55.4K/1203 行) | 多智能体（TradingAgents）配置/运行 |
| `/evaluations` | `EvaluationsPage` | `Evaluations.tsx` (19.4K) | 策略回测评估 |
| `/history` | `HistoryPage` | `History.tsx` (16.9K) | 交易/信号历史 |
| `/datasources` | `DataSourcesPage` | `DataSources.tsx` (42.0K) | 数据源配置/健康检查 |
| `/settings` | `SettingsPage` | `Settings.tsx` (**69.6K/1452 行，第二大**) | 全局设置：语言、时区、主题、模型、PAT、模板导入等 |
| `/analysis/:symbol/:date` | `AnalysisDetailPage` | `AnalysisDetail.tsx` (22.8K) | 单标的深度分析详情 |

### 2.3 导航与响应式
- 桌面端：顶部 floating 卡片导航（前 5 项直接展示，其余收入 `AccountMenu` 下拉）。
- 移动端：顶部栏 + 底部固定 tab 栏（同样前 5 项），使用 `pb-[env(safe-area-inset-bottom)]` 适配刘海屏。
- **`RequireAuth` 守卫**：基于 `localStorage` 的 `token` / `token_expires` 判定，未登录 `Navigate` 到 `/login`。
- 顶栏含版本号拉取（`appApi.version()`）、GitHub 链接、`LogsModal`、`SelfCheckModal`、`NotificationBell`，以及版本更新检查（`/settings/update-check`，弹窗可忽略）。

---

## 3. 状态管理与 Hooks

### 3.1 全局状态模型
**无 Redux/Zustand**。全局状态由「Provider + 自定义 hooks + 模块级单例」承载：
- `main.tsx` 装配的 Provider（嵌套顺序）：`MarketColorProvider` → `BrowserRouter` → `ToastProvider` → `ApplicationConfirmProvider` → `App`。
- `App.tsx` 内再套：`RequireAuth` → `GuidedTourProvider`（新手引导）→ `NotificationProvider`（通知）。
- `NotificationProvider` 内部组合 `useAssistantActivity(true)`（活动任务流）与 `useNotifications(...)`（通知轮询），通过 Context 向 `NotificationBell` 及子面板下发。

### 3.2 关键 Hooks（`src/hooks/`）

| Hook | 职责 | 实现要点 |
| --- | --- | --- |
| `useQuoteRefresh` | **行情实时刷新**核心 | 自调度轮询：用 `running` 标志避免慢请求重叠；`document.hidden` 时暂停轮询；监听 `visibilitychange`/`online` 唤醒；`AbortController` 可取消；间隔由 `intervalSeconds` 决定。页面（如 `Stocks`）传入具体刷新函数。 |
| `useNotifications` | 通知摘要/未读轮询 | 自适应轮询间隔：`activeCount>0`→5s、隐藏标签→30s、否则 15s；单次请求去重（`request.current`）；监听 `ASSISTANT_ACTIVITY_CHANGED`/`NOTIFICATIONS_CHANGED`/`online`/`focus` 即时唤醒；变更才重拉未读列表；失败标记 `disconnected`。 |
| `use-theme` | 明暗主题 | `mode` ∈ `light/dark/system`；`system` 跟随 `prefers-color-scheme` 并实时响应 OS 变化；结果写 `document.documentElement.classList`。 |
| `useAssistantTask` | 助手任务生命周期 | 编排 `chatApi` 的流式发送/订阅、审批、重试、上下文压缩（17KB，复杂状态机）。 |
| `useAssistantActivity` | 助手活动流/任务进度 | 配合 SSE/轮询获取活动任务与通知。 |
| `useChatAutoScroll` | 聊天自动滚动 | 含对应单测 `useChatAutoScroll.test.tsx`。 |
| `use-avatar` | 用户头像获取/缓存 | — |
| `use-market-colors` | 涨跌配色（红涨绿跌/绿涨红跌本地化） | 与 `lib/market-colors.ts`、`main.tsx` 的 `applyMarketColorScheme` 联动。 |

**性能要点**：`useQuoteRefresh` 与 `useNotifications` 均通过「防重叠 / 去重请求 / 后台降频 / 隐藏暂停」避免高频重渲染与无效网络，是实时性的关键且对性能友好。

---

## 4. 国际化（i18n）

基于 **i18next**，但做了按需加载与工程化约束。

### 4.1 语言与切换
- 支持 **`zh-CN`（默认）** 与 **`en-US`**（`i18n/index.ts` 的 `SUPPORTED_LOCALES`）。
- 初始语言探测：`localStorage('panwatch-locale')` → 浏览器语言（`zh` 优先中文）→ 默认。
- `changeLocale()` 调用 `i18n.changeLanguage`，触发 `languageChanged` 事件 → `applyLocale()`：
  - 设置 `document.documentElement.lang`、页面标题（`common:product.pageTitle`）、description、apple-mobile-web-app-title；
  - **切换 manifest 链接**：中文用 `/manifest.zh-CN.json`、英文用 `/manifest.json`；
  - 写回 `localStorage`。
- `interface-language.ts` 的 `useInterfaceLanguage()` + `syncInterfaceLanguage()` 在语言变化时 **PUT `/settings/ui_language`** 到后端，使后台 AI 任务也使用同一语言（前后端语言一致）。

### 4.2 按需加载（不在入口打包）
`i18n/loaders.ts` 把每个 `(locale, namespace)` 映射到动态 `import('./locales/...')`，自定义 `BackendModule.read` 在 i18next 需要时才加载，避免字典进入主包。命名空间：`common`/`navigation`/`auth`/`configuration`/`settings`/`bizUi`。

### 4.3 双语资源
`src/i18n/locales/{zh-CN,en-US}/` 下各 6 个文件（common/navigation/auth/configuration/settings/biz-ui），结构对称。错误文案在 `api/client.ts` 内维护一份英文映射（`API_ERROR_TEXT_EN`），结合后端 `error_code` 做本地化；中文环境直接展示后端 `body.message`。

### 4.4 时区处理
- 无 dayjs/moment，统一走 `Intl.DateTimeFormat`（`i18n/format.ts` 的 `formatDate/formatNumber/formatCurrency/formatMarketCurrency`，按 `CNY/HKD/USD` 与 `CN/HK/US` 市场映射）。
- `base-ui/datetime.ts` 严格区分 **instant（带时区偏移的 ISO）** 与 **date-only 市场日期**：`browserTimezone()` 取浏览器时区；`toLocalDateTimeInput()` 仅用于把 instant 填入本地时间输入；`expiryToISO()` 把本地墙钟转回 ISO 并处理 DST 重复小时。
- 项目提供 `scripts/check-timezones.mjs` 在 `pnpm build` 时静态校验时区用法，强制一致性。

---

## 5. PWA：可安装到手机主屏的机制

`public/` 与 `index.html` 提供完整 PWA 支撑（**非 Workbox，手写 SW**）：

| 文件 | 作用 |
| --- | --- |
| `public/manifest.json` | 英文 Manifest：`name=PanWatch`、`display=standalone`、`orientation=portrait-primary`、192/512 图标、`categories=[finance,productivity]`、`lang=en-US` |
| `public/manifest.zh-CN.json` | 中文 Manifest：`name=盯盘侠 PanWatch`、`short_name=盯盘侠`、`lang=zh-CN` |
| `public/sw.js` | Service Worker：缓存 `panwatch-v2`；`install` 预缓存静态、`skipWaiting`；`activate` 清理旧缓存 + `clients.claim` |
| `public/icon.svg` / `icon-192.png` / `icon-512.png` | 安装图标（支持 `maskable`） |

**可安装链路：**
1. `index.html` 声明 `<link rel="manifest" href="/manifest.json">` 及 iOS 相关 meta（`apple-mobile-web-app-capable`、`apple-touch-icon` 等）。
2. `index.html` 底部脚本在 `load` 后 `navigator.serviceWorker.register('/sw.js')`。
3. 满足「可安装」条件（HTTPS/localhost + 有效 manifest + 已注册 SW）后，移动端/桌面出现 **「添加到主屏幕 / 安装」**；`display=standalone` 使主屏图标进入后无浏览器 chrome，呈类原生 App。
4. **语言联动 manifest**：`applyLocale()` 按当前语言切换 manifest 链接，安装时即带对应语言元数据。

**缓存策略（`sw.js` fetch 事件）：**
- 同源请求、且非 `/api/`：采用 **网络优先 + 失败回退缓存**（实时数据场景：先取网络，成功则更新缓存，失败再用缓存）。
- `/api/` 请求**不缓存**，直接走网络（保证行情/通知等实时性）。

---

## 6. 内部 UI 包拆分（packages/）

`pnpm-workspace.yaml` 纳入 `packages/*`。`packages/README.md` 明确：页面统一从 `@panwatch/api` 发请求，**禁止在页面里直接 `fetch`**；UI 原子从 `base-ui` 取。三个包均为 `private`、`type: module`、以 `src/index.ts` 为 `exports` 入口。

### 6.1 `@panwatch/api`（数据/通信层，依赖图最底层）
- 出口 `src/index.ts` 汇聚：`client`、`locale`、`sse`、`types`、以及 20+ 领域模块（stocks/insight/app/auth/recommendations/discovery/dashboard/portfolio/factors/health/home/paper-trading/chat/tradingagents/datasources/pats/evaluations/notifications/onboarding）。
- **`client.ts`（HTTP 基建）**：
  - `API_BASE='/api'`，统一 `fetchAPI<T>(path, options)`；自动附加 `Authorization: Bearer <token>`；`AbortController` + 可配 `timeoutMs`(默认 20s)；401 触发 `logout()` 跳转 `/login`。
  - 响应约定 `{ code, success, data, message, error_code }`，`code!==0 || success===false` 时抛本地化错误（`localizedApiError`）。
  - 维护 `getToken/isAuthenticated/logout`、大段英文错误码映射（`API_ERROR_TEXT_EN`，覆盖 auth/agent/ai/paper_trading/price_alert 等领域）。
- **`sse.ts`（SSE 基建，关键）**：
  - 用 `fetch + ReadableStream` 手写 SSE（因原生 `EventSource` 无法带 `Authorization`）。
  - `readSSE()`：单次连接消费到流结束，返回 `lastEventId`（断线续推用）；支持 `Last-Event-ID` 请求头。
  - `subscribeSSE()`：**自动重连订阅（GET）**，指数退避（1/2/4/8/8s…），带 `Last-Event-ID` 续推；提供 `onRetry/onFailed/onClosed` 回调（重试用尽由调用方降级轮询）。
- **`chat.ts`（助手流式）**：定义全套对话/任务/上下文/审批类型；`sendMessageStream`（POST 流，断线经持久化任务事件流 `…/tasks/{id}/events` + Last-Event-ID 续推，最多重连 3 次）、`subscribeAssistantTaskStream`、`decideAssistantApprovalStream`；回调含 `onToken/onToolCallStart/onToolResult/onPlan/onApprovalRequired/onPaused/onDone/onTrace/onError` 等，并且把 `task_created/run_started/tool_result/done/error` 等事件同步喂给 trace 面板。
- **`auth.ts`**：`authApi.status/login/setup`，token 含 `expires_at`。
- **不依赖任何 UI 包**，是依赖图最底层。

### 6.2 `@panwatch/base-ui`（底层原子组件 + 工具）
- `src/index.ts` 仅 `export * from './cn'`（类名合并 `clsx+tailwind-merge` 风格的 `cn`）。
- `components/ui/` 为 **shadcn/ui 原子集**：badge、button、calendar（基于 `react-day-picker`）、confirm-dialog（含 `useConfirm`/`ConfirmProvider`）、dialog、hover-popover、input、label、popover、select、skeleton、switch、tabs、toast（含 `useToast`/`ToastProvider`）。
- `datetime.ts`：时区/日期工具（见 4.4）。
- 纯展示、无业务语义，**被 `biz-ui` 与 `src` 共同依赖**。

### 6.3 `@panwatch/biz-ui`（业务复合组件 + 领域工具）
- `src/index.ts` 极薄（`export * from './...'`），导出领域工具与组件。
- 领域工具：`market.ts`（市场/行情领域）、`analysis-sections.ts`（分析章节定义）。
- `components/`：**组合 base-ui 原子 + api 领域模型**的可复用业务组件，如 `InteractiveKline`、`KlineModal`、`DeepAnalysisModal`、`StockInsightModal`、`PriceAlertFormDialog`、`StockPriceAlertPanel`、`AnalysisReader`、`AnalysisMetadata`、`AiSuggestionBadge`、`TechnicalBadge`、`ToolkitDiagnostics`、`AddPositionCalculator`、`BadgeChip`、`AmbientBackground`、`LogsModal` 等。
- 被 `src` 页面直接消费（如 `App.tsx` 引用 `LogsModal`、`AmbientBackground`；`Stocks` 引用多个 biz-ui 弹窗/面板）。

**依赖关系（已核实）：**
```
@panwatch/api   (最底层，无 UI 依赖)
      ↑
@panwatch/base-ui (原子 UI + cn/datetime 工具)
      ↑
@panwatch/biz-ui (业务复合组件 + 领域工具，依赖 base-ui + api)
      ↑
src (页面/路由，依赖三者)
```
验证：`Stocks.tsx` 同时 import `@panwatch/api`、`@panwatch/base-ui/components/ui/*`、`@panwatch/biz-ui/components/*`；`App.tsx` 同理。

---

## 7. 前端与后端通信

- **统一出口**：所有请求经由 `@panwatch/api` 的 `fetchAPI`（页面不得直接 `fetch`）。
- **REST/HTTP**：`client.ts` 封装 baseURL `/api`、Bearer 鉴权、超时、错误归一化与本地化；领域端点各模块（如 `dashboardApi.homeApi.portfolioApi.recommendationsApi` 用于 Dashboard；`stocksApi`/`klinesApi` 用于 Stocks；`chatApi` 用于助手）。
- **SSE / 流式**：`sse.ts` + `chat.ts` 提供三类流式：
  1. 助手消息流 `POST /assistant/conversations/{id}/messages/stream`（打字机 `onToken`）；
  2. 任务事件流 `GET /assistant/tasks/{id}/events`（进度/工具/审批，可订阅+重连）；
  3. 审批流 `POST /assistant/approvals/{id}/decision/stream`。
  均支持 **`Last-Event-ID` 续推 + 断线重连（≤3 次）**，保证弱网/服务重启后不丢事件。
- **通知/活动**：`useNotifications` 轮询 `/notifications/summary`+`/list`；`useAssistantActivity` 获取活动任务；`NotificationProvider` 据此弹 toast、角标、抽屉。
- **错误边界**：`src/components/RouteBoundary.tsx` 提供 `RouteErrorBoundary`（路由级）与 `RouteLoadingFallback`（Suspense 兜底）；`App.tsx` 对每个路由包 `RouteErrorBoundary`+`Suspense`。传输层错误在 `client.ts` 转本地化文案。
- **鉴权流程**：`Login.tsx` 调 `authApi.setup/login` 拿 `token`+`expires_at` 存 `localStorage`；`RequireAuth` 与 `fetchAPI` 的 401 处理共同守卫。

---

## 8. 设计风格（UI_GUIDELINES）

存在 `UI_GUIDELINES.md` 与 `UI_GUIDELINES.zh-CN.md`（内容对称）。核心规范：

- **复用 base-ui 原子**：新增交互 UI 必须复用 `packages/base-ui/src/components/ui` 原语（Button/Dialog/Select/Toast/Popover/Confirm 等），不得手写原生 `<select>`/`<option>`、浏览器 `confirm/alert/prompt`，不得用未加 `scrollbar` 类的原生滚动区。
- **滚动区**：`overflow-auto` 等元素加 `scrollbar` 类（透明轨道 + 主题化细滑块，明/暗双主题）；嵌套 flex 滚动用 `min-h-0` + `overscroll-contain`；表头用 sticky 且不透明。
- **确认与反馈**：用 `useConfirm()`（在应用本地化 `ConfirmProvider` 下），mutation 写在 await 之后；成功/失败用 `useToast`；对话框用共享 `Dialog`、上下文面板用 `Popover`。
- **工程化强制**：`pnpm check:ui`（= `scripts/check-ui-primitives.mjs`）在 PR 校验与 `pnpm build` 中解析 TS，**拒绝原生 select/dialog、浏览器弹窗调用、未加样式的原生滚动区**，无 legacy 豁免。另有 `check:i18n`（禁止硬编码文案）、`check:market-colors`（涨跌幅配色校验）、`check:timezones`（时区校验）。
- 视觉 QA 要求：浮动面板需验证明/暗、桌面/移动、开合、键盘焦点、Esc、滚动内容。

**设计语义归纳**：卡片化（`card` 类）浮动导航与面板、渐变主色 `primary`（`#4f46e5` indigo，manifest `theme_color`）、`success` 用于增益、涨跌配色由 `use-market-colors` + `market-colors.ts` 按本地化切换（红涨绿跌/绿涨红跌），整体为「暗色优先 + 玻璃质感卡片」的金融监控风格。

---

## 9. 关键观察与建议

1. **包内依赖干净**：api → base-ui → biz-ui → src 单向分层清晰，页面不直接 `fetch`，利于维护。建议保持。
2. **实时性设计成熟**：`useQuoteRefresh`（防重叠/后台暂停）与 `useNotifications`（自适应轮询/去重）对性能友好；助手用自建 SSE + Last-Event-ID 续推，断线体验优于裸 EventSource。
3. **i18n 工程化到位**：按需加载 + 双语 manifest 联动 + 后端语言同步 + 硬编码文案 lint，国际化质量高。
4. **超大页面风险**：`Stocks.tsx`（2607 行）、`Settings.tsx`（1452 行）、`Agents.tsx`（1203 行）、`Dashboard.tsx`（843 行）体量偏大。因已路由级懒加载，首包影响可控；但单文件维护成本高，建议按业务（行情/持仓/分析/提醒）进一步拆分或抽取 hooks。
5. **PWA 策略合理**：API 不缓存、静态资源网络优先+缓存兜底，契合实时金融产品；唯 `sw.js` 未预缓存 `index.html` 之外的 JS/CSS chunk（仅预缓存图标/manifest），离线可启动但首屏资源仍依赖网络——如需更强离线，可加入构建产物预缓存。
6. **时区严格**：全程 `Intl` + 专用工具函数 + 构建期 lint，避免了常见时区 bug，是该项目的工程亮点。

---

## 10. 已核实章节对照

| 需求章节 | 状态 | 关键证据文件 |
| --- | --- | --- |
| 技术栈 | ✅ 已核实 | `frontend/package.json`、`vite/tailwind/tsconfig` |
| 路由结构/懒加载/数据预载 | ✅ 已核实 | `src/router/page-loaders.ts`、`src/App.tsx` |
| 状态管理与 hooks（含 useQuoteRefresh、通知 Provider） | ✅ 已核实 | `src/hooks/*`、`src/components/notifications/NotificationProvider.tsx` |
| 国际化（i18n、中英切换、tz） | ✅ 已核实 | `src/i18n/*`、`base-ui/datetime.ts`、`interface-language.ts` |
| PWA（manifest、SW、桌面快捷方式） | ✅ 已核实 | `index.html`、`public/sw.js`、`public/manifest*.json` |
| 内部 UI 包拆分（api/base-ui/biz-ui 职责与依赖） | ✅ 已核实 | `packages/README.md`、三包 `package.json` + 关键源码 |
| 前后端通信（API 封装、SSE/流式、错误边界） | ✅ 已核实 | `api/client.ts`、`api/sse.ts`、`api/chat.ts`、`components/RouteBoundary` |
| 设计风格（UI_GUIDELINES） | ✅ 已核实 | `UI_GUIDELINES.md`、四个 check 脚本 |
| 关键页面精读（Dashboard/Stocks/Agents/Settings） | ✅ 已核实结构与职责 | 四页 import 图/规模/API/hook 信号扫描 |
