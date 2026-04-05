# ARCHITECTURE

## 架构状态
当前为基线导入后的第一版，已结合实际代码结构补充本地运行与检索实现要点。

## 目标架构原则
- 本地优先
- 文件可审计
- Skill-first 检索优先
- 基线稳定与实验扩展分离
- 文档化决策优先于口头约定

## 初步技术方向

### 参考基线
- 后端：Python + FastAPI + SSE
- 前端：Next.js + React + TypeScript
- 检索：Skill-first + 向量检索 + BM25 + 融合
- 编排：LangGraph / Agent 工作流

### 我们的初步分层
- 根目录 `*.md`：长期记忆与项目管理层
- `backend/`：FastAPI API、LangGraph agent、memory、knowledge、skills、tools
- `src/frontend/`：Next.js 前端工作台
- `docs/`：补充设计与学习材料
- `.vscode/`：本地 demo / 学习用编辑器配置
- 未来预留 `experiments/`：新 RAG 方向实验层

## 模块设计草案

### 1. 项目治理模块
- 负责项目记忆文件维护
- 负责任务、状态、决策管理

### 2. 基线应用模块
- 负责复现参考仓库中的最小可运行系统
- 目标包括前端、后端、知识目录、技能目录与配置文件
- 当前已确认的运行方式：
  - 后端使用 `backend/.venv` + `uvicorn app:app --port 8004`
  - 前端使用 `src/frontend` 下 `npm run dev`，默认访问 `http://127.0.0.1:3000`

### 3. 检索编排模块
- 保留 Skill-first 主链路
- 在证据不足时触发混合检索兜底
- 后续在此处挂接多模态与图结构扩展
- 当前已确认的实现细节：
  - `knowledge_retrieval/indexer.py` 会同时准备 BM25 与向量索引
  - 没有 embedding key 时，BM25 仍可用，向量索引会关闭
  - 知识问答最终回答仍依赖 LLM，因此“完整知识问答”需要可用 API Key

### 4. 实验扩展模块
- 用于隔离多模态 RAG、GraphRAG / RAGGraph、评测实验
- 原则上不直接污染已验证的基线主链路

## 接口原则
- 优先保留可直接启动与可直接验证的接口
- 外部接口变更需有明确动机和记录
- 新实验模块尽量通过适配层接入，而非直接改坏基线

## 数据与目录原则
- 文档、知识、技能、配置优先以文件落盘
- 生成物和缓存应有明确目录
- 实验数据与正式基线数据尽量隔离

## 当前未决架构问题
- 是否严格保留上游目录结构
- 是否需要在初始化阶段就引入 `experiments/` 分层
- 默认采用哪家模型供应商作为开发与 demo 标配
## 2026-03-26 架构补充
- 主回答链路与工具调用链路已拆分：
  - 主回答模型继续使用 `LLM_*` 配置
  - 工具调用型 agent 使用独立的 `TOOL_LLM_*` 配置
- 当前 Kimi 接入的默认运行方式：
  - 回答模型：`kimi-k2.5`
  - 工具模型：`moonshot-v1-8k`
  - Base URL：`https://api.moonshot.cn/v1`
- 向量检索支持两类 embedding 路径：
  - OpenAI-compatible 远程 embedding provider
  - 本地 HuggingFace embedding provider
- 当前 demo 默认采用本地 embedding provider，以便在没有额外商业 embedding key 时也能完成向量检索验证。
## 2026-03-26 第三次补充
- 前端验证层补充 `Playwright`
- 当前浏览器级回归入口：
  - `src/frontend/scripts/verify-chat-ui.mjs`
  - `scripts/dev/run-chat-ui-verification.ps1`
- 目的：
  - 用真实浏览器验证聊天区滚动稳定性
  - 验证每轮 assistant 消息下方的 token 用量展示
  - 验证 knowledge 路由后的前端展示是否包含来源路径
## 2026-03-26 Fourth Update
- Frontend layout is now viewport-constrained:
  - `page.tsx` keeps the full three-panel app inside `h-screen`.
  - Sidebar, chat panel, and inspector use internal scrolling instead of page-level growth.
- Chat streaming UX is stabilized through two layers:
  - internal bottom-stick logic in `ChatPanel.tsx`
  - CSS scroll stabilization via stable scrollbar gutter and contained overscroll
- Knowledge routing flow is now regex-only:
  - regex rules decide whether a request goes to the knowledge route
  - no extra router-classifier model is called during route selection
- Verification layers now cover:
  - API-level routing checks through `backend/scripts/verify_knowledge_routing.py`
  - browser-level chat checks through `src/frontend/scripts/verify-chat-ui.mjs`
## 2026-03-26 Fifth Update
- Startup architecture now separates service readiness from retrieval warmup:
  - FastAPI can reach `/health` before the knowledge index finishes rebuilding
  - knowledge-index warmup runs in the background after app startup
  - startup warmup is BM25-first and skips vector construction
- Import-time performance is now protected by lazy loading:
  - `knowledge_retrieval` package exports are lazy
  - model SDK imports in `graph/agent.py` are deferred until a model client is built
- Frontend bootstrap now has an explicit degraded state:
  - backend-unavailable startup is represented in app state
  - chat UI renders a retry banner instead of crashing the page on initial fetch failure
## 2026-03-26 Ninth Update
- Tool orchestration now assumes the real local environment:
  - tool prompts describe a Windows PowerShell workspace
  - common Linux `find /workspace/...` calls are normalized inside the terminal tool
  - `read_file` is ordered ahead of shell execution for repository inspection tasks
## 2026-03-27 Twelfth Update
- Baseline architecture direction has changed back toward upstream compatibility:
  - the core chat path again centers on one primary chat-model builder
  - the knowledge path again uses the upstream-style orchestrator wiring
  - provider-specific branches are no longer part of the intended baseline architecture
- Local-only conveniences remain outside the core runtime path:
  - VS Code tasks and launch settings
  - root startup scripts
  - frontend readability tweaks
## 2026-03-27 Thirteenth Update
- Startup architecture keeps one deliberate local deviation from upstream:
  - health readiness is separated from heavy orchestration and index warmup imports
  - lazy imports are used for `langchain` and `llama-index` components on the backend critical path
- Reason:
  - the fully restored upstream import path is not viable on this Windows machine because it prevents the backend from reaching `/health` in time for the local demo workflow
## 2026-03-27 Twenty-Second Update
- Runtime configuration now includes an explicit execution-platform selector:
  - `config.json` stores `execution_platform` alongside `rag_mode`
  - the supported values are `windows` and `linux`
- Prompt and tool behavior now read from the same runtime setting:
  - `prompt_builder.py` emits Windows PowerShell guidance when `execution_platform=windows`
  - `prompt_builder.py` emits Linux bash guidance when `execution_platform=linux`
  - `terminal_tool.py` launches the matching shell and only applies platform-specific command normalization for that shell
- Frontend control surface:
  - the navbar now exposes a visible Win/Linux toggle
  - the toggle is hydrated from the backend during app initialization and persisted through the config API
