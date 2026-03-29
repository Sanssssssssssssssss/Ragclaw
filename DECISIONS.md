# DECISIONS

## D-001 外部化项目记忆
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 使用 `PROJECT_BRIEF.md`、`REQUIREMENTS.md`、`ARCHITECTURE.md`、`DECISIONS.md`、`TASKS.md`、`STATE.md` 作为长期记忆。
- 原因：
  - 聊天上下文不可靠，项目需要可持续、可交接、可复盘。

## D-002 私有仓库作为主线
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 以我们的私有 GitHub 仓库作为后续更新与改造的主线存储。
- 原因：
  - 需要私有化管理、持续更新与实验控制。

## D-003 先做基线复现，再做扩展研究
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 第一阶段先复现参考仓库的最小可运行能力，再进入多模态 RAG、GraphRAG / RAGGraph 等扩展。
- 原因：
  - 没有稳定基线就难以比较改造效果，也会放大调试成本。

## D-004 初始化阶段默认本地优先
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 第一阶段优先保证本地开发可运行，不引入复杂部署基础设施。
- 原因：
  - 可以更快验证框架、依赖、检索链路和实验方向。

## D-005 “GitHub page private” 的临时解释
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 将“GitHub page 上但是 private”按“GitHub 私有仓库”执行。
- 原因：
  - 用户已明确当前阶段先私有，未来若项目完全演进后再考虑公开。

## D-006 仓库导入与同步策略
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 采用“新建私有仓库作为 `origin`，上游开源仓库作为 `upstream`，将上游历史合并进主仓”的策略。
- 原因：
  - 既满足私有主线开发，也保留了上游提交历史与后续同步能力。

## D-007 第一阶段以本地 demo 友好性优先
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 第一阶段补齐 VS Code 配置、本地启动任务与运行说明，以便快速演示和学习源码。
- 原因：
  - 当前目标是基于上游框架学习与改造，本地可运行和可观察性比过早重构更重要。

## D-008 默认聊天模型切换到 Kimi
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 第一阶段默认聊天模型按 Kimi 接入，配置入口写入 `backend/.env`。
- 原因：
  - 用户计划申请 Kimi API，并希望项目后续围绕该模型进行学习和演示。

## D-009 统一使用根目录一键启动脚本
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 在仓库根目录提供统一启动脚本，负责同时拉起前后端。
- 原因：
  - 减少重复命令，便于未来 demo、学习和交接。

## D-010 Kimi K2.5 的当前可用运行参数
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 当前项目接入 Kimi K2.5 时，默认使用 `https://api.moonshot.cn/v1`
  - 默认设置 `LLM_TEMPERATURE=1`
- 原因：
  - 这是本机当前 API key 的实测可用组合；`.ai` 端点返回 `401`，`.cn` 端点可正常完成真实请求

## D-011 Kimi 下的知识检索兼容策略
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 当 `SkillRetrieverAgent` 在当前模型下发生工具调用兼容错误时，自动降级到 hybrid retrieval，而不是直接让请求失败
- 原因：
  - 先保证知识问答链路稳定可演示，再在后续迭代里专门处理 Kimi 的工具调用兼容问题

## D-012 当前开发后端默认端口改为 8014
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 本地开发默认后端端口从 `8004` 调整为 `8014`
  - 启动脚本在启动前端时自动写入 `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8014/api`
- 原因：
  - 当前机器上的 `8004` 存在异常监听占用，影响稳定启动；切换端口能保证脚本即开即用
## D-013 聊天模型与工具模型分离
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 主回答链路继续使用 `kimi-k2.5`
  - 所有依赖 LangChain `create_agent` 的工具调用型 agent 默认改用 `moonshot-v1-8k`
- 原因：
  - 实测 `kimi-k2.5` 在多轮工具调用时会触发 `reasoning_content is missing` 错误，而 `moonshot-v1-8k` 可稳定完成工具调用

## D-014 向量检索默认补齐方案
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 为知识索引增加本地 HuggingFace embedding provider
  - demo 默认使用本地 embedding provider 补齐向量检索能力
- 原因：
  - 在 Moonshot embedding 能力未完成官方文档核实前，本地方案更稳、更符合当前学习和演示目标

## D-015 上下文建立必须经过 Markdown 记忆文件
- 状态：Accepted
- 日期：2026-03-26
- 决策：
  - 每一轮开始任务前，必须先基于 `PROJECT_BRIEF.md`、`REQUIREMENTS.md`、`ARCHITECTURE.md`、`DECISIONS.md`、`TASKS.md`、`STATE.md` 重建上下文
- 原因：
  - 避免把聊天上下文当作唯一记忆来源，确保项目可以跨多轮、跨会话稳定延续
## 2026-03-26 第三次更新
- D-016 浏览器级回归验证采用 Playwright
  - 状态：Accepted
  - 决策：前端引入 `playwright` 作为开发依赖，并提供仓库内的 UI 验证脚本
  - 原因：当前问题涉及滚动抖动、knowledge 展示和 token 用量，必须做真实浏览器验证
- D-017 当前开发后端默认端口切换到 `8015`
  - 状态：Accepted
  - 决策：根目录启动脚本、前端默认 API 端口和本地说明统一切换到 `8015`
  - 原因：这台机器上的 `8014` 存在不稳定监听状态，会干扰对最新代码的验证
## 2026-03-26 Fourth Update
- D-018 Viewport-constrained app layout
  - Status: Accepted
  - Decision: Keep the main app inside the viewport and force scrolling to happen inside each panel rather than on the whole page.
  - Reason: This removes the visible page jump during streaming responses and makes Playwright verification meaningful.
- D-019 Two-stage knowledge router
  - Status: Accepted
  - Decision: Use regex as a high-recall prefilter, then call a lightweight router model only for prefiltered candidates.
  - Reason: Regex alone was too brittle and caused both false positives and false negatives.
- D-020 Per-turn token usage in chat UI
  - Status: Accepted
  - Decision: Persist assistant-turn usage in backend history and render `Input … · Output … tokens` under assistant messages.
  - Reason: The project is used for learning and demos, so cost/usage visibility should be first-class.
- D-021 Modified functions require docstrings
  - Status: Accepted
  - Decision: Functions touched by this repair pass must include docstrings or JSDoc comments with I/O summary plus purpose.
  - Reason: This project is a long-lived learning codebase, so local readability is part of maintainability.
- D-022 VS Code friendly root startup
  - Status: Accepted
  - Decision: The root `start-dev.ps1` script should be runnable directly from a VS Code integrated terminal and launch backend/frontend via separate PowerShell processes.
  - Reason: This is the user's primary local workflow, and WMI-based process checks were too unstable on this machine.
## 2026-03-26 Fifth Update
- D-023 Graceful frontend behavior when backend startup lags
  - Status: Accepted
  - Decision: Frontend bootstrap requests must fail into a visible retry state instead of throwing an unhandled runtime overlay when the backend is not yet reachable.
  - Reason: The project is used for demos and learning, so cold-start lag should look like an understandable app state, not a broken app.
- D-024 Keep backend startup import path lightweight
  - Status: Accepted
  - Decision: Move knowledge-index warmup to the background and lazy-load heavy knowledge/model modules so `/health` can come up quickly.
  - Reason: Fast backend readiness is more important than blocking startup on retrieval warmup, and the knowledge index already has its own readiness status.
## 2026-03-26 Sixth Update
- D-025 Frontend user actions share one connection-error surface
  - Status: Accepted
  - Decision: User-triggered frontend actions that depend on the backend should set the shared connection error state instead of surfacing unhandled promise rejections.
  - Reason: This keeps the UI behavior consistent when the backend is temporarily unavailable and makes failures easier to understand during demos.
## 2026-03-26 Seventh Update
- D-026 Keep orchestration imports off the backend startup critical path
  - Status: Accepted
  - Decision: Agent-construction imports such as `langchain.agents.create_agent` must be lazy inside execution paths instead of running at module import time.
  - Reason: Module-level orchestration imports were able to block backend startup before `uvicorn` opened the health endpoint.
- D-027 Do not restore persisted vector indexes during app bootstrap
  - Status: Accepted
  - Decision: `KnowledgeIndexer.configure()` should initialize storage and manifest state only; vector-index restoration belongs to lazy retrieval or background warmup paths.
  - Reason: Persisted local vector indexes can pull heavy embedding-model initialization into startup and prevent the backend from reaching a ready state.
## 2026-03-26 Eighth Update
- D-028 Roll back knowledge routing to regex-only
  - Status: Accepted
  - Decision: Remove the lightweight classifier stage and let the knowledge router use regex rules only for now.
  - Reason: The project needs a simpler and more predictable baseline while backend hangs are still being debugged; the latest confirmed requirement supersedes D-019.
## 2026-03-26 Ninth Update
- D-029 Tool agents must target the local Windows workspace explicitly
  - Status: Accepted
  - Decision: The system prompt and terminal tool should steer agent tool use toward Windows PowerShell syntax, backend-relative workspace paths, and `read_file` before shelling out.
  - Reason: The current local environment is Windows, and Linux-style `/workspace` plus GNU `find` commands caused tool failures and stalled responses.
## 2026-03-29 Retrieval Constraint Update
- D-030 Keep knowledge hallucination control prompt-first, not guard-heavy
  - Status: Accepted
  - Decision: Remove the runtime answer-guard step from the knowledge QA path and keep only stronger knowledge-answer prompt constraints.
  - Reason: The project needs lighter runtime behavior; the first fix should come from retrieval-path cleanup plus stricter answer instructions, not a heavy post-generation guard.
- D-031 Startup should restore the persisted vector index before the app reports ready
  - Status: Accepted
  - Decision: Run knowledge-index warm start during FastAPI lifespan startup instead of leaving vector restoration entirely in a detached background task.
  - Reason: Benchmark and live UI need a truthful `vector_ready` state right after startup; otherwise they can observe a false BM25-only window.
- D-030 Startup knowledge warmup is BM25-first
  - Status: Accepted
  - Decision: App startup should rebuild the knowledge manifest and BM25 state first, while skipping vector construction on the startup path.
  - Reason: Local vector warmup can take too long for demo startup and leaves the frontend stuck in a long-running rebuilding state.
## 2026-03-26 Tenth Update
- D-031 Ordinary chat bypasses the tool agent
  - Status: Accepted
  - Decision: Messages without explicit workspace or tool intent should go directly to the main chat model instead of entering the tool-calling agent loop.
  - Reason: General questions such as broad AI discussion do not need local tools, and sending them through the tool agent caused unnecessary stalls.
## 2026-03-27 Eleventh Update
- D-032 Main chat now uses raw HTTP completions for Kimi instead of the OpenAI SDK path
  - Status: Accepted
  - Decision: The main answer path sends direct HTTP requests to `/chat/completions`, uses `max_tokens=512`, and then chunks the final text for the UI.
  - Reason: Live debugging showed `kimi-k2.5` was returning quickly but consuming the completion budget on `reasoning_content`; the previous SDK path masked the real response shape and left the UI stuck in `Thinking`.
## 2026-03-27 Twelfth Update
- D-033 Restore the core runtime path to upstream-compatible behavior first
  - Status: Accepted
  - Decision: The backend core orchestration is rolled back toward `upstream/main` for `config.py`, `graph/agent.py`, `api/chat.py`, `app.py`, `knowledge_retrieval/indexer.py`, `knowledge_retrieval/skill_retriever_agent.py`, `tools/__init__.py`, `tools/terminal_tool.py`, and `graph/prompt_builder.py`.
  - Reason: The project drifted too far from the upstream baseline, and stacked local fixes made the main chat and retrieval path harder to reason about than the original repository.
- D-034 Postpone Kimi-specific behavior until after the upstream-compatible baseline is stable
  - Status: Accepted
  - Decision: Kimi-specific routing, tool-model splitting, raw HTTP completion handling, and local-only embedding special cases are no longer treated as part of the baseline runtime path.
  - Reason: The current project goal is to behave like the upstream reference first; provider-specific optimization comes only after the baseline is stable again.
## 2026-03-27 Thirteenth Update
- D-035 Keep startup-critical imports and retrieval warmup off the backend health path
  - Status: Accepted
  - Decision: Even after restoring upstream-compatible runtime behavior, `langchain` and `llama-index` heavy imports stay lazy, and knowledge-index rebuild stays off the initial `/health` critical path.
  - Reason: On this Windows machine, restoring those imports and synchronous warmup to module-import and lifespan startup caused the backend to stall before the health endpoint became reachable.
## 2026-03-27 Fourteenth Update
- D-036 Use a non-thinking Kimi model for the local main chat path
  - Status: Accepted
  - Decision: The local runtime now points the main chat path at `kimi-k2-turbo-preview`, and the provider setting uses the existing OpenAI-compatible configuration path for the Moonshot endpoint.
  - Reason: The current priority is to stop the main chat flow from being dominated by thinking-model behavior while keeping the change limited to configuration only.
## 2026-03-27 Fifteenth Update
- D-037 Keep the main chat builder aligned with Moonshot's current temperature restriction
  - Status: Accepted
  - Decision: The upstream-style chat-model builder now uses `temperature=1` instead of the previously hard-coded `0`.
  - Reason: The current Moonshot-compatible non-thinking Kimi model on this project returns `400 invalid temperature` unless the request uses `1`.
## 2026-03-27 Sixteenth Update
- D-038 Use official K2.5 non-thinking mode instead of changing to another model
  - Status: Accepted
  - Decision: The local main chat path switches back to `kimi-k2.5` and sends Moonshot's official `thinking: {"type": "disabled"}` request body through the OpenAI-compatible client.
  - Reason: Official Kimi documentation now confirms that `kimi-k2.5` supports disabling thinking directly, which lets the project keep the intended model while avoiding long reasoning-first responses.
- D-039 Align K2.5 non-thinking temperature with Moonshot's current requirement
  - Status: Accepted
  - Decision: When `kimi-k2.5` runs with `thinking=disabled`, the local chat-model builder uses `temperature=0.6`; when thinking stays enabled, it keeps `1`.
  - Reason: Moonshot's current chat API specifies fixed temperature behavior for K2.5, and any other value returns `400 invalid temperature`.
## 2026-03-27 Seventeenth Update
- D-040 Use `extra_body` for K2.5 thinking control and omit explicit temperature in non-thinking mode
  - Status: Accepted
  - Decision: `kimi-k2.5` non-thinking mode is now passed through `ChatOpenAI(extra_body={"thinking": {"type": "disabled"}})`, and the builder omits `temperature` instead of forcing `0.6`.
  - Reason: `model_kwargs` was merged into unsupported SDK kwargs and caused a client-side error, while the live Moonshot endpoint still rejected explicit `0.6`; omitting temperature is the safest compatible path.
## 2026-03-27 Eighteenth Update
- D-041 Keep `python_repl` stateless but preload common analysis imports and compress common failures
  - Status: Accepted
  - Decision: `python_repl` continues to run each snippet in a fresh subprocess, but now preloads `Path` and `pandas as pd` when available, returns compact guidance for common `NameError` / `openpyxl` failures, and documents the stateless execution rule in the runtime prompt.
  - Reason: The current local workflow benefits more from predictable isolated execution plus clearer feedback than from a larger stateful REPL redesign, and the live failures were dominated by missing preload imports and missing Excel dependencies.
## 2026-03-27 Nineteenth Update
- D-042 Frontend state is split by concern to keep streaming chat from re-rendering the full workspace
  - Status: Accepted
  - Decision: The frontend no longer relies on one monolithic app context for all consumers; layout, chat, session, runtime, and inspector state are exposed through separate contexts, and message subtrees use memoization or deferred rendering where helpful.
  - Reason: Streaming token updates were forcing unrelated panels such as Monaco inspector and navbar controls to re-render on every chunk, which made the UI feel sluggish even when the backend was healthy.
- D-043 Buffered token rendering is preferred over per-chunk UI updates
  - Status: Accepted
  - Decision: The chat UI now batches streamed token chunks into short 40ms flush windows before applying them to React state.
  - Reason: Rendering every incoming chunk immediately caused excessive markdown recomputation and layout work, which made the page feel much slower than the actual backend response time.
## 2026-03-27 Twentieth Update
- D-044 Normalize common bash and cmd shell idioms before running the terminal tool on Windows
  - Status: Accepted
  - Decision: The terminal tool should rewrite high-frequency non-PowerShell commands such as `test -d ... && ... || ...`, `ls -la`, and `findstr ... | head -100` into Windows PowerShell-safe equivalents before execution.
  - Reason: The current local environment is Windows PowerShell, and surfacing raw parser errors for these predictable command shapes makes the tool feel broken even when the intent is straightforward.
- D-045 Force UTF-8 subprocess I/O for `python_repl` on Windows
  - Status: Accepted
  - Decision: `python_repl` subprocesses should run with `-X utf8`, `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`, and explicit UTF-8 decoding so Chinese output does not crash on the local console code page.
  - Reason: The live UI failures showed valid UTF-8 file content being blocked by Windows `gbk` console encoding rather than by the user's code intent.
## 2026-03-27 Twenty-Second Update
- D-046 Runtime execution platform is user-selectable
  - Status: Accepted
  - Decision: The project now treats shell environment as runtime config with explicit `windows` and `linux` values, exposed through the frontend instead of being hard-coded in prompts and tools.
  - Reason: The same codebase needs to run on both Windows and Linux, and the current tool guidance was drifting into the wrong shell syntax for the active environment.
- D-047 Use `python -m pip` as the terminal-safe default package-management form
  - Status: Accepted
  - Decision: Terminal command normalization should rewrite leading `pip` and `pip3` invocations to `python -m pip`.
  - Reason: This is more reliable across local venv setups on both Windows and Linux, and it avoids common `pip` command-not-found failures in the live UI.
## 2026-03-27 Twenty-Third Update
- D-048 Emulate `pdftotext` through a local Python helper instead of depending on an external binary
  - Status: Accepted
  - Decision: Terminal execution now rewrites `pdftotext` calls to a local `scripts/pdf_extract_text.py` helper that uses Python PDF readers.
  - Reason: The current local environments do not reliably ship Poppler binaries, so PDF text extraction should not depend on external command availability.
- D-049 Add PDF-library compatibility shims inside `python_repl`
  - Status: Accepted
  - Decision: `python_repl` now exposes a `pypdf` compatibility alias backed by `PyPDF2` when needed, creates a minimal `pdfplumber` fallback for text extraction, and normalizes `subprocess.run(["pip", ...])` to `python -m pip`.
  - Reason: The live failures were caused by the model choosing common PDF libraries and pip-install patterns that were reasonable in intent but not available by default in this backend environment.
## 2026-03-27 Twenty-Fourth Update
- D-050 Formal knowledge ingestion now covers PDF, XLSX, and TXT
  - Status: Accepted
  - Decision: `knowledge_retrieval/indexer.py` now scans `.pdf`, `.xlsx`, and `.txt` files, parses them into persisted chunk records with file-specific metadata, and feeds those chunks into the existing BM25 and vector indexing flow.
  - Reason: Previously only `.md` and `.json` entered the formal index, which forced PDF and workbook retrieval to depend on directory notes, skill heuristics, or ad hoc tool reads instead of stable indexed recall.
- D-051 Local embedding support remains part of the retrieval baseline
  - Status: Accepted
  - Decision: `EMBEDDING_PROVIDER=local` is again treated as a first-class backend option, and the indexer builds or reloads persisted vector indexes through `HuggingFaceEmbedding` when no remote embedding API key is present.
  - Reason: This repository's actual local runtime already uses a local sentence-transformer model, so removing local embedding support silently disabled vector retrieval in practice.
- D-052 Hybrid fallback is allowed for all formally indexed knowledge types
  - Status: Accepted
  - Decision: the knowledge orchestrator now treats `md`, `json`, `txt`, `pdf`, and Excel workbook types as valid fallback targets for vector/BM25 retrieval.
  - Reason: Restricting fallback to `md/json` left newly indexed PDF and workbook chunks underused even after they were added to the index.
## 2026-03-27 Twenty-Fifth Update
- D-053 Backend benchmark coverage lives in a small first-party module
  - Status: Accepted
  - Decision: benchmark cases, evaluation, runner logic, and result output now live under `backend/benchmarks`, with results written to `backend/storage/benchmarks` and a PowerShell wrapper in `scripts/dev/run-backend-benchmarks.ps1`.
  - Reason: The project needed a backend-only regression harness that can be rerun without the frontend and can score routing, retrieval, tool use, explicit constraint following, and groundedness.
- D-054 Infrastructure failures are reported separately from capability scores
  - Status: Accepted
  - Decision: benchmark results now classify provider-level failures such as rate limits, missing API keys, and connection interruptions as infrastructure skips instead of folding them into routing/retrieval/tool accuracy.
  - Reason: Provider quota failures were making benchmark runs look like product regressions when the underlying capability logic had not actually been exercised.
## 2026-03-28 Twenty-Sixth Update
- D-055 Benchmark selection is suite/module/subtype based
  - Status: Accepted
  - Decision: the benchmark runner now selects cases through `suite` (`smoke` / `full`) or `module` (`rag`, `routing`, `tool`, `constraints`, `groundedness`), with `rag_subtype` support for `retrieval`, `grounding`, `ranking`, and `table`.
  - Reason: Running the full benchmark on every iteration wastes tokens and slows RAG-focused debugging, so the harness needs focused entrypoints.
- D-056 RAG cases live in subtype-specific editable files
  - Status: Accepted
  - Decision: RAG benchmark cases now live under `backend/benchmarks/rag/` in separate JSON files per subtype instead of being mixed into one large case file.
  - Reason: The user wants to hand-maintain QA pairs and expand harder retrieval, ranking, grounding, and table evaluations incrementally without touching unrelated benchmark cases.
- D-057 Judge scoring is optional and additive
  - Status: Accepted
  - Decision: a lightweight OpenAI-compatible judge client is available only for RAG cases and augments, rather than replaces, the rule-based benchmark metrics.
  - Reason: Rule-based checks remain the stable baseline, while judge-model scoring should be easy to enable or skip depending on local API availability and cost.
## 2026-03-28 Twenty-Seventh Update
- D-058 RAG benchmark filtering is question-aware and modality-aware
  - Status: Accepted
  - Decision: benchmark selection now supports `question_type`, `difficulty` range, `modalities`, and `sample_per_type` filtering so only the needed subset of RAG cases is loaded and run.
  - Reason: Manual RAG QA sets will grow over time, and the user needs to target specific failure shapes such as negation, fuzzy retrieval, or PDF/XLSX questions without paying for unrelated cases.
- D-059 Judge configuration accepts lightweight env aliases
  - Status: Accepted
  - Decision: the judge client now accepts both uppercase (`JUDGE_*`) and lowercase (`judge_*`) environment variable names for base URL, API key, model, and timeout.
  - Reason: This keeps the judge layer flexible across local shells and future automation scripts without introducing a separate configuration system.
## 2026-03-28 Twenty-Eighth Update
- D-060 `question_type` is a first-class benchmark aggregation dimension
  - Status: Accepted
  - Decision: benchmark summaries now emit dedicated per-question-type metrics for `direct_fact`, `compare`, `negation`, `fuzzy`, `multi_hop`, and `cross_file_aggregation`, including judge-based metrics when available.
  - Reason: The user wants to evaluate and sample by failure shape directly, not just use `question_type` as an input filter.
- D-061 Manual RAG gold paths are normalized on load
  - Status: Accepted
  - Decision: case loading now normalizes `gold_sources`, `gold_chunks`, and `gold_tables` to canonical `knowledge/...` paths and decodes legacy `#Uxxxx` path escapes.
  - Reason: Hand-maintained QA files are easier to author when they do not need to perfectly mirror internal manifest path formatting.
## 2026-03-28 Twenty-Ninth Update
- D-062 RAG-only summaries omit non-applicable tool and constraint metrics
  - Status: Accepted
  - Decision: when a benchmark slice contains only RAG cases and no tool/constraint expectations, summary fields such as `tool_selection_accuracy`, `constraint_following_accuracy`, and `forbidden_action_violation_rate` now emit `null` instead of misleading `0`.
  - Reason: A RAG-only benchmark should not look like it failed tool or constraint logic when those dimensions were never exercised.
- D-063 RAG cases default to the knowledge route unless overridden
  - Status: Accepted
  - Decision: evaluator-side route expectation now defaults `module=rag` cases to `expected_route="knowledge"` when the case file does not explicitly override it.
  - Reason: This makes `route_accuracy` meaningful for RAG slices without forcing the user to backfill every existing RAG case file.
- D-064 Cross-file retrieval is evaluated with coverage, not any-hit
  - Status: Accepted
  - Decision: `question_type=cross_file_aggregation` now records `source_coverage` and requires multi-source coverage to pass retrieval, instead of treating a single matching source as success.
  - Reason: Cross-file aggregation questions are about combining multiple files, so any-hit scoring overstated retrieval quality.
## 2026-03-28 Thirtieth Update
- D-065 Remote embeddings now target Bailian `text-embedding-v4`
  - Status: Accepted
  - Decision: local runtime configuration now sets `EMBEDDING_PROVIDER=bailian` with `EMBEDDING_MODEL=text-embedding-v4` and DashScope compatible-mode base URL, replacing the previous local HuggingFace embedding default on this machine.
  - Reason: the user wants future vector indexing and RAG retrieval to use Bailian's managed embedding model instead of the local embedding stack.
## 2026-03-28 Thirty-First Update
- D-066 Knowledge startup warmup now restores persisted vector indexes instead of disabling them
  - Status: Accepted
  - Decision: backend startup now calls `knowledge_indexer.warm_start()` to load the persisted knowledge manifest and any existing vector store, rather than rebuilding the manifest with `build_vector=False` and clearing vector readiness.
  - Reason: once a knowledge vector index has been built and persisted, a backend restart should continue using it without forcing a manual rebuild every time.
- D-067 Bailian embeddings use a generic OpenAI-compatible embedding adapter
  - Status: Accepted
  - Decision: the knowledge indexer now wraps Bailian `text-embedding-v4` with a lightweight custom OpenAI-compatible embedding adapter instead of relying on `llama_index`'s OpenAI model enum.
  - Reason: `llama_index.embeddings.openai.OpenAIEmbedding` rejected `text-embedding-v4`, which prevented both vector-index building and vector-index reload under the Bailian embedding configuration.
## 2026-03-28 Thirty-Second Update
- D-068 Skill-first knowledge retrieval is now runtime-switchable
  - Status: Accepted
  - Decision: runtime config and the frontend navbar now expose a `Skill on/off` toggle; when off, the knowledge orchestrator skips the skill retriever entirely and goes straight to vector/BM25 hybrid retrieval plus fusion.
  - Reason: the user needs a fast way to test formal indexed retrieval behavior without skill-first short-circuiting hiding vector/BM25 participation.
## 2026-03-28 Thirty-Third Update
- D-069 Indexed knowledge QA now uses formal retrieval as the default main path
  - Status: Accepted
  - Decision: for knowledge questions targeting already-indexed `md/json/pdf/txt/xlsx` content, the orchestrator now uses only the formal indexed retrieval path (`vector + bm25 + fused`) and no longer falls back to skill or general-purpose file-reading tools as the main path.
  - Reason: retrieval-focused evaluation should expose the true quality of indexing, chunking, and retrieval instead of being silently repaired by skill/tool backreads.
- D-070 Weak single-channel evidence no longer counts as a full indexed-retrieval success
  - Status: Accepted
  - Decision: the knowledge orchestrator now returns `partial` unless vector and BM25 provide corroborating evidence for overlapping indexed sources; only corroborated indexed evidence is treated as a full `success`.
  - Reason: this keeps knowledge QA honest when retrieval is weak and avoids presenting low-confidence matches as if the knowledge base clearly answered the question.
## 2026-03-28 Thirty-Fourth Update
- D-071 Benchmark retrieval scoring now groups PDFs with their `*_extracted.txt` companions into one source family
  - Status: Accepted
  - Decision: evaluator path matching now normalizes `knowledge/.../*.pdf` and the sibling `knowledge/.../*_extracted.txt` into the same source family when computing retrieval hit and source coverage.
  - Reason: the persisted index may legitimately retrieve an extracted-text companion for a PDF-backed source, and benchmark scoring should not mark that as a miss just because the file path differs.
## 2026-03-29 Thirty-Fifth Update
- D-072 Formal RAG retrieval now uses lightweight multi-query expansion before fusion
  - Status: Accepted
  - Decision: the formal knowledge orchestrator now expands each indexed-retrieval query into a small rewrite set plus entity/keyword hints, retrieves candidates across those variants, and then fuses them before answer-time evidence selection.
  - Reason: PDF fuzzy, compare, multi-hop, and cross-file questions were under-recalled by a single raw query, but the user explicitly wants a lighter-weight retrieval improvement rather than a new heavy agent or guard subsystem.
- D-073 Parent merge and source diversification happen after retrieval, not inside indexing
  - Status: Accepted
  - Decision: retrieval still operates on child chunks, but the orchestrator now merges sibling hits by `parent_id`, tracks supporting locators, and applies a lightweight diversified pick over source families before answer generation.
  - Reason: this keeps the index and chunker simple while improving evidence completeness and reducing single-source crowding for compare and aggregation questions.
## 2026-03-29 Thirty-Sixth Update
- D-074 Cross-file PDF evaluation now prioritizes the final diversified evidence order
  - Status: Accepted
  - Decision: benchmark trace construction now lists source paths from the last knowledge step with results before earlier retrieval stages, so `top_k` source-hit and coverage metrics reflect the final evidence actually sent into answer generation.
  - Reason: cross-file retrieval already selected multiple report families in the final diversified step, but benchmark scoring was still dominated by earlier vector/BM25 candidate order and underreported real coverage.
- D-075 Knowledge-answer scaffolds now steer negation and multi-hop questions away from internal notes and extra examples
  - Status: Accepted
  - Decision: the agent now removes raw retrieval `Status/Reason` strings from the hidden knowledge context, adds a lightweight negation scaffold, and strengthens multi-hop instructions to stay within the explicitly requested entities and supported evidence.
  - Reason: partial negation answers were leaking internal retrieval notes into user-facing prose, and one multi-hop answer was adding an extra medical product plus an unsupported page citation.
## 2026-03-29 Thirty-Seventh Update
- D-076 Token metrics now separate model-call usage from session-trace volume
  - Status: Accepted
  - Decision: knowledge/direct-answer model completions now save per-message `usage` with prompt/output token estimates, `/api/tokens/session/{id}` now returns both `model_call_total_tokens` and `session_trace_tokens`, and the frontend header shows both values side by side.
  - Reason: the old single token number mixed the final model call with all persisted retrieval-step text, which made a one-question knowledge session appear to consume ~64k tokens even though the actual final model call was much smaller.
