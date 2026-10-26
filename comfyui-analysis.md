# ComfyUI 架构分析

> 仓库：comfyanonymous/ComfyUI（节点式 AI 创作引擎，本地扩散模型推理）
> 分析来源：GitHub Contents API 抓取并本地解码的源码（`main.py`、`server.py`、`comfy_execution/graph.py`、README.md、关键目录清单）。
> 说明：受匿名 API 限速影响，`comfy/model_management.py` 与 `app/__init__.py` 未逐行读取，相关章节基于 README 与已知事实并标注「待深读」。

---

## 0. 一句话结论

ComfyUI 是一个**前后端一体、以节点图（node graph）为核心的本地扩散模型推理引擎**：Python 后端负责节点执行、模型加载与显存调度，前端（独立 TS 仓库编译进 PyPI 包）用 LiteGraph 画布做可视化编排；通过 `custom_nodes` 扩展机制实现极强的模块化，整个系统可完全离线运行，且以"异步权重流送 + 智能 VRAM/RAM 卸载"在消费级显卡（最低 4GB VRAM）上跑最大开源模型。

---

## 1. 定位与三大仓库

README 明确 ComfyUI 由**三个独立仓库**组成（非单仓单体）：

| 仓库 | 角色 |
| --- | --- |
| `comfyanonymous/ComfyUI`（本仓库，Core） | 推理引擎 + 节点执行 + 后端服务 + 模型管理 |
| `Comfy-Org/ComfyUI_frontend` | 前端（TypeScript/Vue，编译为 PyPI 包 `comfyui-frontend-package` 安装进 Core） |
| `Comfy-Org/ComfyUI` Desktop | 桌面端壳（Windows/macOS 一键安装） |

> 关键事实：**主仓库不含前端源码**。`web/` 路径在仓库中不存在（404），前端以编译产物包形式随依赖安装；可用 `--front-end-version Comfy-Org/ComfyUI_frontend@latest` 切换每日构建/特定版本（`README` 第 415-433 行）。

---

## 2. 整体架构（分层）

```
main.py                  ← 进程入口：CLI 解析、日志、custom_nodes 预启动、VRAM/设备初始化、启动 PromptServer
  │
  ├─ server.py           ← PromptServer（Flask + WebSocket）：HTTP 路由、/ws 实时进度、PromptQueue 队列
  ├─ comfy_execution/    ← 节点图执行引擎（graph.py 拓扑/部分重执行、caching 缓存、jobs 任务、validation）
  ├─ nodes.py            ← 内置节点注册表（109KB，所有原生节点在此定义 INPUT_TYPES/OUTPUT_NODE）
  ├─ comfy/              ← 底层核心（69 文件）：model_management（显存）、samplers、ldm/k_diffusion、clip/controlnet/lora
  ├─ api_server/         ← 新版 FastAPI 服务（routes/services/utils），与新前端/API 对齐
  ├─ comfy_api/          ← 版本化 API（v0_0_1 / v0_0_2），节点 IO 类型契约
  ├─ app/                ← 用户/模型/数据库/扩展管理（user_manager、model_manager、database、custom_node_manager）
  ├─ custom_nodes/       ← 扩展节点（第三方注入，前/后端双注册）
  └─ models/ input/ output/  ← 权重目录、用户资源、生成产物
```

依赖方向（推断自导入）：`main.py` → `server.PromptServer` → `comfy_execution` → `nodes`/`comfy`；`api_server` 与 `server.py` 并存（新旧两套入口，README 仍以 `python main.py` 为主）。

---

## 3. 启动与入口（`main.py` 实测）

`main.py`（30KB）启动流程（实测）：

1. **CLI 解析**：`comfy.options.enable_args_parsing()` + `comfy.cli_args`（端口、--cuda-device、--listen、--enable-manager、--front-end-version 等）。
2. **日志/治理**：`app.logger.setup_logger`、`app.governance`（合规拒绝）、`app.product_tips`。
3. **自定义节点预启动**：扫描 `custom_nodes/`，执行各节点的 `prestartup_script.py`（`main.py:220-230`），记录耗时；支持 `--disable_all_custom_nodes` + `--whitelist_custom_nodes`。
4. **显存/设备初始化**：`enables_dynamic_vram()` 时启用 `comfy_aimdo.control`（动态 VRAM 头room + NVML 压力感知，`main.py:81-83`）；Windows 下默认强制单 GPU（`CUDA_VISIBLE_DEVICES=0`，`main.py:50-57`）。
5. **启动服务**：最终构造并 run `PromptServer`（Flask）。

要点：启动时**先加载节点、后起服务**，自定义节点的 `prestartup_script` 在导入期执行，是扩展生态的钩子点。

---

## 4. 节点执行引擎（`comfy_execution/`，实测 `graph.py`）

`comfy_execution/graph.py`（392 行）是图执行的核心抽象：

- **`DynamicPrompt`**：封装一次提交的工作流（`original_prompt`），并支持：
  - `node_overrides`：运行时覆盖某节点而不改原始提交（实现"参数微调重跑"）；
  - `ephemeral_prompt` / `ephemeral_parents` / `ephemeral_display`：执行期动态生成的中间节点（如预览、子图展开），可溯源到真实节点（`get_real_node_id`）。
  → 这支撑了 README 宣传的 **"partial graph re-execution（部分图重执行）"**：只重算变动分支，复用已有缓存。
- **拓扑与依赖**：`DependencyCycleError`（环检测）、`NodeNotFoundError`、`NodeInputError`；`_update_blocked_by`（文件末尾，第 379-391 行）用反向传播计算各节点的 `blocked_by` 集合，得到可执行的就绪节点列表——即** Kahn 式拓扑排序**，是节点调度的骨架。
- **`ExecutionBlocker`**（已迁至 `graph_utils.py`，避免 torch 过早导入）：节点可返回 blocker 中断下游（如条件不满足）。
- 同目录另有 `caching.py`（结果缓存）、`jobs.py`（任务生命周期）、`validation.py`（图校验）、`progress.py`（进度状态）——构成完整的执行子系统。

> 新执行引擎与旧 `execution.PromptExecutor`（`server.py` 引用，70KB）并存：README/目录显示 ComfyUI 正从 `execution.py` 迁移到 `comfy_execution/`，二者职责一致（后者更模块化）。

---

## 5. 后端服务（`server.py` + `api_server/`）

### 5.1 `server.py`（PromptServer，Flask）
`server.py` 定义 `class PromptServer`（实测），是**经典入口**：
- 基于 Flask 提供 HTTP 路由 + **WebSocket（`/ws`）** 实时推送进度/预览图；
- 持有 `PromptQueue`（任务队列，实测 `server.py` 含 `PromptQueue` 引用），与执行线程解耦；
- 兼具静态文件托管（前端编译产物）与 API。

### 5.2 `api_server/`（新版 FastAPI）
目录结构 `api_server/{__init__.py, routes/, services/, utils/}`（实测），是向 FastAPI + 分层路由/服务迁移的新后端，对应 `comfy_api/` 的版本化契约（v0_0_1/v0_0_2）。`app/` 下还有 `user_manager`、`model_manager`、`database/`（Alembic 迁移）、`governance`、`agent_host` 等——表明 ComfyUI 已从纯单机工具演进为带用户/模型/资产治理的多用户可部署系统。

---

## 6. 前端（独立仓库，LiteGraph 画布）

- **技术栈**：`ComfyUI_frontend` 为 TypeScript + Vue，编译后作为 `comfyui-frontend-package` 安装进 Core（README 第 416-431 行）。主仓库无前端源码。
- **画布**：基于 **LiteGraph.js**（自研/轻量节点画布），非 React 重型框架；节点拖拽、连线、参数面板均由前端实现，后端仅暴露节点 schema（`INPUT_TYPES`）与执行结果。
- **版本切换**：`--front-end-version Comfy-Org/ComfyUI_frontend@<ver>` 可在双周稳定版与每日构建间切换。
- **App Mode**：复杂工作流可封装为简单 UI 暴露给终端用户（README "App Mode"）。

---

## 7. 节点系统（`nodes.py` + `custom_nodes/`）

- **内置节点**：`nodes.py`（109KB）定义全部原生节点，每个节点实现 `INPUT_TYPES()`（输入 schema，分 required/optional/hidden）、`OUTPUT_NODE`（是否输出）、`CATEGORY`、执行函数。新节点类型契约在 `comfy_api/latest/_io` 与 `comfy.comfy_types.node_typing`。
- **扩展机制（`custom_nodes/`）**：
  - 第三方包放入 `custom_nodes/`，启动时 `main.py` 扫描并加载，执行 `prestartup_script.py` 钩子；
  - 节点在前端（schema）与后端（逻辑）**双注册**；
  - `app/custom_node_manager.py` 提供管理；官方生态由 **ComfyUI-Manager**（`--enable-manager` 启用）提供一键安装/更新。
- **蓝图**：`blueprints/` 目录含 **118 个示例工作流 JSON**（图像修复、深度估计、视频生成 LTX/Wan、音频生成 Stable Audio 等），既是示例也是功能模块。

---

## 8. 模型与显存管理（`comfy/`，README + 已知事实）

> `comfy/model_management.py` 因限速未逐行读，以下基于 README 与公开事实。

- **智能 VRAM/RAM 管理**：自动在显存不足时将模型/中间张量卸载到内存，需要时再搬回（`model_management` + `memory_management`）。
- **异步权重流送（asynchronous weight streaming）**：README 宣称可在 **4GB VRAM + 8GB RAM** 上相对快速地跑最大的开源模型（"saturating your GPU compute"）——通过分块流式加载权重而非整模型常驻显存。
- **量化支持**：支持多种量化模型以降低显存占用。
- **硬件覆盖**：NVIDIA / AMD / Intel / Apple Silicon / Ascend；动态 VRAM 模式（`comfy_aimdo`）。
- **模型格式**：原生支持 `safetensors` / `ckpt`、diffusers 转换（`diffusers_load`/`diffusers_convert`），CLIP/ControlNet/LoRA 等均在 `comfy/` 下。

---

## 9. API 与工作流格式（`comfy_api/`）

- **版本化 API**：`comfy_api/{v0_0_1, v0_0_2, latest}`，节点 IO 用 `_io` 契约，前端/后端/第三方按版本对齐。
- **两套工作流格式**：
  - **Prompt/API 格式**：后端执行用的紧凑节点图（`{id: {class_type, inputs}}`），对应 `comfy_execution.graph.DynamicPrompt`；
  - **UI/workflow 格式**：前端画布用的完整图（含位置/连线），可互相转换；`blueprints/` 即此类。
- **队列**：`PromptQueue` 异步排队，WebSocket 推送进度与预览，支持中断/重执行。

---

## 10. 扩展生态

- **custom_nodes**：核心扩展点（第三方算法/模型接入）。
- **ComfyUI-Manager**：`--enable-manager` 启用，生态包管理。
- **Partner Nodes**：接入闭源模型（Nano Banana / Seedance / Hunyuan3D 等，README 第 42 行），需联网或 `--offline`/`--disable-partner-nodes` 控制。
- **子图（subgraph）**：`app/subgraph_manager.py` 支持工作流复用为子图。

---

## 11. 部署（README）

| 形态 | 说明 |
| --- | --- |
| **Desktop** | 官方桌面应用（Windows/macOS），最易上手 |
| **Manual** | `git clone` + 装 torch/依赖 + `python main.py`（需 Python 3.10+、对应 CUDA torch） |
| **Portable** | 便携包（Windows） |
| **Cloud** | Comfy Cloud（付费，免本地硬件） |

- 依赖：`torch` + `torchvision` + 项目 `requirements.txt`；模型权重放 `models/`（需自行下载，不随代码分发）。
- 端口：默认 `8188`（`--port`），`--listen` 暴露、`--cuda-device` 选卡。
- 全离线：除 Partner Nodes/Manager 外，核心推理完全离线。

---

## 12. 可借鉴点 & 风险

**可借鉴**：
1. **节点图 + 部分重执行 + 缓存**：`DynamicPrompt.ephemeral`/`node_overrides` 让"只重算变动分支"成为一等公民，值得任何 DAG 执行引擎参考。
2. **扩展即目录**：`custom_nodes/` + 预启动钩子 + 双注册，极低门槛的生态扩展模式。
3. **显存流送**：异步权重流送在消费级硬件跑大模型，是端侧推理的工程范本。
4. **前端独立仓库 + 版本化前端包**：后端稳定、前端高频迭代互不阻塞（`--front-end-version`）。

**风险/注意**：
1. **新旧后端并存**：`server.py`（Flask）与 `api_server/`（FastAPI）过渡期，API 行为以哪套为准需看版本，集成时易踩坑。
2. **模型权重需自管**：`models/` 不随代码，部署必须解决权重来源（合规/版权）。
3. **custom_nodes 安全**：第三方节点 `prestartup_script` 在导入期执行，等同运行其代码，需信任源（Manager 审核机制）。
4. **源码未全覆盖**：本报告 `comfy/model_management.py`、`app/__init__.py`、前端源码未逐行读取，显存章节为事实性概述，深读需另行抓取。
