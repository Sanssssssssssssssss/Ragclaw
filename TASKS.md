# TASKS

## 待办 Todo
- T-008 形成基线代码与实验代码的分层方案
- T-009 规划多模态 RAG 研究入口
- T-010 规划 GraphRAG / RAGGraph 研究入口
- T-011 建立评测与对比基线
- T-012 在本地写入 Kimi API Key 并完成连通性验证
- T-013 验证真实流式聊天回答链路
- T-014 验证“知识检索 + LLM 回答”的完整链路

## 进行中 In Progress
- 暂无

## 已完成 Done
- T-001 初始化项目记忆文件与协作协议落地
- T-002 确认私有仓库创建与上游同步策略（采用新私有仓库 + upstream 合并）
- T-003 拉取并导入参考仓库代码到当前项目
- T-004 记录本地启动与依赖安装流程
- T-005 跑通后端健康检查
- T-006 跑通前端开发环境
- T-007 验证至少一条本地知识检索链路（BM25）
- T-015 增加仓库根目录一键启动脚本
- T-016 增加 Kimi 模型配置入口与文档说明
- T-017 放大前端主要阅读与演示字号
- T-018 修正一键启动脚本的端口占用处理逻辑

## 阻塞 Blocked
- T-012 在本地写入 Kimi API Key 并完成连通性验证
- T-013 真实流式聊天回答链路验证
- T-014 “知识检索 + LLM 回答”的完整链路验证

## 当前阻塞原因
- 当前提供的 Kimi Code key 与本项目现有 Web 接法不兼容
- 在认证通过前，真实聊天与真实知识问答都无法完成

## 任务说明
- 所有任务完成后需同步更新 `STATE.md`
- 关键结构性变化需同步更新 `DECISIONS.md`
- 新功能进入范围前需能映射到 `REQUIREMENTS.md`

## 2026-03-26 更新
- 已完成：T-012 在本地写入可用的 Kimi API Key，并验证 `kimi-k2.5` 连通性
- 已完成：T-013 验证真实流式聊天回答链路
- 已完成：T-014 验证“知识检索 + LLM 回答”的完整链路
- 已完成：T-019 将开发后端默认端口切换到 `8014`，避免本机异常占用的 `8004` 干扰 demo
- 已完成：T-020 为 Kimi K2.5 增加温度配置与知识检索降级兼容
- 新待办：T-021 评估并修复通用工具调用 agent 与 Kimi K2.5 的原生兼容性
## 2026-03-26 第二次更新
- 已完成：T-021 修复 Kimi 下通用工具调用 agent 的原生兼容性，采用聊天模型与工具模型分离方案
- 已完成：T-022 增加本地 embedding provider，并补齐可验证的向量检索链路
- 已完成：T-023 增加工具 agent 与向量检索的独立验证脚本
- 后续待办：T-024 评估是否保留 `SkillRetrieverAgent` 的降级策略，或在未来恢复 K2.5 的原生多轮工具调用
## 2026-03-26 第三次更新
- 已完成：T-025 为前端安装 Playwright 开发依赖并补齐浏览器安装脚本
- 已完成：T-026 新增聊天 UI 浏览器级验证脚本 `src/frontend/scripts/verify-chat-ui.mjs`
- 已完成：T-027 新增 `scripts/dev/start-backend-dev.ps1`、`scripts/dev/start-frontend-dev.ps1`、`scripts/dev/run-chat-ui-verification.ps1`
- 待继续验证：T-028 在稳定拉起前后端后，完成一次完整的 Playwright 聊天区回归验证并记录结果
## 2026-03-26 Fourth Update
- Completed: T-029 Fix the streaming chat viewport so the page no longer jumps while assistant responses grow.
- Completed: T-030 Show per-turn input/output token usage under assistant messages.
- Completed: T-031 Replace regex-only knowledge routing with regex prefilter plus lightweight classifier.
- Completed: T-032 Add docstrings/JSDoc comments to functions touched in this repair pass.
- Completed: T-033 Verify routing through the live API and verify chat UI behavior through Playwright.
- Follow-up: T-034 Clean remaining legacy mojibake strings outside the chat-area files that were fixed in this pass.
- Completed: T-035 Rework `start-dev.ps1` so it can be launched directly from a VS Code integrated terminal.
## 2026-03-26 Fifth Update
- Completed: T-036 Make frontend startup resilient when the backend is unavailable, so `Failed to fetch` no longer crashes the page.
- Completed: T-037 Shorten backend cold-start critical path by moving knowledge-index warmup and heavy knowledge/model imports off the initial import path.
- Completed: T-038 Update `start-dev.ps1` to wait for backend health and print frontend/backend ready state in the VS Code workflow.
- Completed: T-039 Re-verify root startup, health endpoint, and browser chat behavior after the startup reliability fixes.
## 2026-03-26 Sixth Update
- Completed: T-040 Route user-triggered frontend action failures into the shared connection error state instead of leaving them as unhandled promise rejections.
- Completed: T-041 Clean visible frontend separator mojibake in token and index-status labels.
- Completed: T-042 Re-run frontend static build after the action-error and copy cleanup.
## 2026-03-26 Seventh Update
- Completed: T-043 Debug why the backend child process never reached `8015` during root startup.
- Completed: T-044 Remove remaining startup-blocking imports from the knowledge orchestration path so backend health can come up quickly.
- Completed: T-045 Re-verify `start-dev.ps1` and `/health` after the backend startup fixes.
## 2026-03-26 Eighth Update
- Completed: T-046 Remove the lightweight knowledge-route classifier and revert routing to regex-only.
- Follow-up: T-047 Continue debugging the remaining `building index` and chat `thinking` hangs after the routing rollback.
## 2026-03-26 Ninth Update
- Completed: T-048 Make tool-agent prompts and terminal execution Windows-aware so Linux-style `/workspace` and GNU `find` calls do not stall the chat flow.
- Completed: T-049 Add backend safeguards against repeated terminal syntax failures and cap the tool-agent recursion depth.
- Completed: T-050 Move startup knowledge warmup to BM25-first mode so the navbar does not stay stuck in long vector rebuilds after boot.
## 2026-03-26 Tenth Update
- Completed: T-051 Route ordinary chat questions directly to the answer model instead of the tool agent unless the message shows explicit tool intent.
## 2026-03-27 Eleventh Update
- Completed: T-052 Debug the real cause of the chat hang down to the upstream model response shape.
- Completed: T-053 Replace the main chat SDK path with a raw Kimi HTTP completion path and raise the completion budget for `kimi-k2.5`.
## 2026-03-27 Twelfth Update
- Completed: T-054 Compare the current backend runtime path against `upstream/main` and identify the main divergence points.
- Completed: T-055 Roll back the core backend orchestration files toward upstream-compatible behavior.
- Completed: T-056 Roll back the prompt/tool path toward upstream-compatible behavior.
- Completed: T-057 Align `.env.example` and the verification scripts with the restored baseline configuration model.
- Next: T-058 Re-test the restored upstream-style baseline locally and record any remaining gaps before reintroducing Kimi-specific logic.
## 2026-03-27 Thirteenth Update
- Completed: T-058 Debug why the restored upstream-style backend no longer reached `/health` through `start-dev.ps1`.
- Completed: T-059 Reintroduce only the minimal lazy-import and background-warmup fixes required for backend startup.
- Completed: T-060 Re-verify `start-dev.ps1 -Restart -NoBrowser` and `/health` after the startup fix.
- Next: T-061 Check chat and knowledge retrieval behavior on top of the restored-and-startable baseline.
## 2026-03-27 Fourteenth Update
- Completed: T-062 Switch the local main chat runtime from `kimi-k2.5` to the non-thinking `kimi-k2-turbo-preview` model.
- Next: T-063 Wait for the user's local run result before deciding whether any further Kimi-specific compatibility work is needed.
## 2026-03-27 Fifteenth Update
- Completed: T-063 Debug the `invalid temperature` chat failure and align the main chat-model builder with Moonshot's current requirement.
- Next: T-064 Wait for the user's local rerun result after the temperature fix.
## 2026-03-27 Sixteenth Update
- Completed: T-064 Confirm the official K2.5 non-thinking request shape and wire it into the OpenAI-compatible chat-model builder.
- Completed: T-065 Switch the local runtime back to `kimi-k2.5` with `LLM_THINKING_TYPE=disabled`.
- Next: T-066 Wait for the user's local rerun result after the K2.5 non-thinking switch.
## 2026-03-27 Seventeenth Update
- Completed: T-066 Debug the client-side `unexpected keyword argument 'thinking'` failure in the K2.5 non-thinking path.
- Completed: T-067 Rework the K2.5 builder to keep `thinking` in `extra_body` and omit explicit temperature in non-thinking mode.
- Next: T-068 Wait for the user's local rerun result after the K2.5 non-thinking builder fix.
## 2026-03-27 Eighteenth Update
- Completed: T-069 Debug the `python_repl` Excel-analysis failures shown in the live UI screenshots.
- Completed: T-070 Preload common analysis imports in `python_repl`, compress common REPL failures into guidance, and add `openpyxl` to backend dependencies.
- Next: T-071 Wait for the user's local rerun result after reinstalling backend dependencies and retrying the same Excel-style question.
## 2026-03-27 Nineteenth Update
- Completed: T-072 Diagnose the main cause of frontend sluggishness during otherwise successful local runs.
- Completed: T-073 Split frontend state by concern, memoize message subtrees, and defer sidebar raw-message rendering to reduce streaming re-render pressure.
- Next: T-074 Wait for the user's local rerun result after the frontend performance pass and collect any remaining hotspots.
## 2026-03-27 Twentieth Update
- Completed: T-074 Reduce chat streaming render frequency by buffering token updates on the frontend.
- Next: T-075 Wait for the user's local rerun result after the token-buffering pass and decide whether any remaining lag needs feature-level tradeoffs.
## 2026-03-27 Twenty-First Update
- Completed: T-076 Normalize the terminal tool's most common bash/cmd command shapes into Windows PowerShell-safe commands.
- Completed: T-077 Force UTF-8 subprocess I/O for `python_repl` and add a compact fallback for Windows console encoding failures.
- Next: T-078 Wait for the user's local rerun result after the terminal and Python tool output compatibility fixes.
## 2026-03-27 Twenty-Second Update
- Completed: T-078 Add a runtime execution-platform setting so the app can switch between Windows PowerShell and Linux bash behavior.
- Completed: T-079 Expose the execution-platform toggle in the frontend navbar and hydrate it from the backend config API.
- Completed: T-080 Make prompt guidance and terminal execution follow the selected execution platform instead of a hard-coded Windows assumption.
- Completed: T-081 Normalize leading `pip` and `pip3` terminal commands to `python -m pip` for cross-platform reliability.
- Next: T-082 Re-verify the Win/Linux runtime toggle in the live UI and collect any remaining shell-specific command shapes that still need normalization.
## 2026-03-27 Twenty-Third Update
- Completed: T-082 Remove the `pdftotext` external-binary dependency from the terminal path by routing it through a local Python helper.
- Completed: T-083 Add PDF compatibility shims in `python_repl` for `pypdf`, `pdfplumber`, and direct `pip` subprocess usage.
- Next: T-084 Wait for the user's next live run and only fix any remaining file-format-specific command shapes that still surface in the UI.
## 2026-03-29 Update
- Completed: T-085 Remove the runtime knowledge answer guard and revert knowledge QA back to prompt-only constraints.
- Completed: T-086 Re-verify one weak-evidence financial-report query and one strong-evidence numeric query against the live indexed retrieval path.
- Next: T-087 Keep watching whether prompt-only knowledge constraints are enough, or whether a smaller retrieval-side signal is still needed for stubborn hallucination cases.
- Completed: T-088 Fix startup vector-index restoration so `vector_ready` is already true when the backend reports ready.
- Completed: T-089 Tighten benchmark knowledge-index gating so RAG runs no longer silently continue in BM25-only mode when embeddings are configured.
## 2026-03-27 Twenty-Fourth Update
- Completed: T-085 Extend the formal knowledge ingestion path so `.pdf`, `.xlsx`, and `.txt` files are scanned, parsed, chunked, and persisted into the knowledge manifest.
- Completed: T-086 Restore local embedding support for the knowledge vector index and lazy vector-index reload.
- Completed: T-087 Widen hybrid retrieval fallback so formally indexed PDF and Excel chunks can participate in vector/BM25 recall.
- Completed: T-088 Rebuild the backend knowledge index with the existing repository files and verify multiformat retrieval hits without starting the frontend.
- Completed: T-089 Add a backend-only benchmark module that scores routing, retrieval, tool use, explicit constraint following, and groundedness through the live backend API.
- Completed: T-090 Add a one-shot PowerShell benchmark runner that starts the backend, waits for health, runs the benchmark suite, saves JSON results, prints a summary, and stops the backend.
- Completed: T-091 Separate infrastructure-level benchmark skips from real capability failures so provider quota issues do not distort routing or retrieval scores.
- Next: T-092 Once provider quota is available again, rerun the benchmark suite to capture a clean non-skipped baseline result file for ongoing regression tracking.
## 2026-03-28 Twenty-Sixth Update
- Completed: T-093 Refactor the benchmark loader so suites and modules can be run independently without always loading the full case set.
- Completed: T-094 Split RAG benchmark cases into subtype-specific JSON files for retrieval, grounding, ranking, and table evaluation.
- Completed: T-095 Extend the benchmark runner and PowerShell wrapper with `--suite`, `--module`, and `--rag-subtype` selection support.
- Completed: T-096 Add an optional judge-model scoring layer for RAG cases with separate judge metrics in the summary.
- Next: T-097 Hand-curate and expand the new RAG subtype case files, especially harder retrieval, ranking, grounding, and table QA pairs.
## 2026-03-28 Twenty-Seventh Update
- Completed: T-097 Extend benchmark selection with `--question-type`, `--difficulty-min`, `--difficulty-max`, `--modalities`, and `--sample-per-type`.
- Completed: T-098 Carry the new RAG metadata fields (`question_type`, `difficulty`, `modalities`) through result payloads and summary aggregation.
- Completed: T-099 Update the current RAG case files so they demonstrate the new schema fields for manual QA maintenance.
- Next: T-100 Hand-author harder negation / compare / fuzzy / PDF+XLSX mixed-modality RAG cases using the new filters and sampling controls.
## 2026-03-28 Twenty-Eighth Update
- Completed: T-100 Promote `question_type` from a filter-only field to a first-class summary aggregation dimension.
- Completed: T-101 Normalize manual RAG gold paths on case load so current QA files can be used without manifest-format hand editing.
- Completed: T-102 Validate the configured judge chain with a minimal Python call against the current Moonshot/Kimi setup.
- Next: T-103 Run a focused live benchmark slice such as `--module rag --question-type compare --sample-per-type 5` once provider quota is available again.
## 2026-03-28 Twenty-Ninth Update
- Completed: T-103 Fix RAG-only benchmark summary metrics so non-applicable tool and constraint dimensions emit `null` instead of misleading zeroes.
- Completed: T-104 Default RAG cases to the `knowledge` route in evaluator logic so `route_accuracy` becomes meaningful without case-file backfills.
- Completed: T-105 Add path normalization and cross-file source coverage scoring so RAG retrieval metrics better match real knowledge-source behavior.
- Completed: T-106 Relax rule-based groundedness with lightweight normalization and partial-coverage thresholds to reduce false negatives versus judge scoring.
- Next: T-107 Re-run focused benchmark slices (`retrieval`, `grounding`, `cross_file_aggregation`, `fuzzy`) and inspect the new metric deltas once provider quota permits.
## 2026-03-28 Thirtieth Update
- Completed: T-107 Switch runtime embedding configuration from local HuggingFace to Bailian `text-embedding-v4`.
- Completed: T-108 Update `.env.example` so future environments have a concrete Bailian embedding configuration example.
- Next: T-109 Restart the backend and rebuild the knowledge vector index under the new Bailian embedding configuration.
## 2026-03-28 Thirty-First Update
- Completed: T-109 Replace knowledge startup warmup with persisted-state recovery so existing vector indexes survive backend restarts.
- Completed: T-110 Add a generic OpenAI-compatible embedding adapter so Bailian `text-embedding-v4` can build and reload llama-index vector stores.
- Completed: T-111 Validate that `warm_start()` now restores `vector_ready=True` against the persisted `backend/storage/knowledge/vector` directory.
- Next: T-112 Restart the live backend once and confirm `/api/knowledge/index/status` stays `vector_ready=true` after a clean process restart.
## 2026-03-28 Thirty-Second Update
- Completed: T-112 Add a runtime `skill_retrieval_enabled` switch to backend config and config API.
- Completed: T-113 Make the knowledge orchestrator skip skill retrieval entirely and force hybrid fallback when the skill switch is off.
- Completed: T-114 Add a `Skill on/off` navbar control and frontend store wiring for the new runtime switch.
- Next: T-115 Restart the dev app and verify the navbar switch flips retrieval traces between `skill-first` and direct `vector/bm25/fused`.
## 2026-03-28 Thirty-Third Update
- Completed: T-115 Converge indexed knowledge QA onto the formal knowledge retrieval path so already-indexed `md/json/pdf/txt/xlsx` questions no longer use skill or general-purpose tool backreads as the main path.
- Completed: T-116 Tighten knowledge retrieval success criteria so weak single-channel evidence downgrades to `partial` instead of pretending to be a full hit.
- Completed: T-117 Stabilize Chinese knowledge-route detection for benchmark-style queries such as `根据知识库...`.
- Next: T-118 Re-run focused PDF/XLSX retrieval and grounding benchmark slices to measure the true formal-retrieval hit rate after removing skill-side repair.
## 2026-03-28 Thirty-Fourth Update
- Completed: T-118 Treat `pdf` and sibling `*_extracted.txt` benchmark hits as one source family during retrieval scoring.
- Next: T-119 Re-run focused PDF benchmark slices and compare source hit/coverage deltas after the evaluator-only source-family normalization.
## 2026-03-29 Thirty-Fifth Update
- Completed: T-119 Add a lightweight indexed-retrieval expansion layer with query rewrites, entity hints, and multi-query candidate recall for formal RAG.
- Completed: T-120 Add heuristic reranking, parent merge, and source-family diversification on top of the formal retrieval candidates without changing the index format.
- Completed: T-121 Re-run targeted PDF retrieval and grounding slices (`fuzzy`, `compare`, `cross_file_aggregation`, `multi_hop`, `negation`) against a clean `vector_ready=true` backend.
- Next: T-122 Investigate why PDF cross-file aggregation coverage still stalls at one-third and decide whether the next cheapest gain is query-side entity decomposition or evidence-pick tuning.
## 2026-03-29 Thirty-Sixth Update
- Completed: T-122 Add a lightweight entity-targeted retrieval supplement for cross-file PDF questions and prioritize final diversified evidence in benchmark trace ordering.
- Completed: T-123 Add lightweight compare / multi-hop / negation scaffolds so answer generation stays within requested entities and supported evidence without introducing a heavy guard system.
- Completed: T-124 Re-run the targeted PDF slices after the focused retrieval + grounding cleanup and save the fresh result to `backend/storage/benchmarks/pdf_targeted_after_focus.json`.
- Next: T-125 Revisit rule-based groundedness criteria for compare / multi-hop PDF cases so the rule-based pass rate better matches the now-clean judge outcomes without hiding real unsupported claims.
## 2026-03-29 Thirty-Seventh Update
- Completed: T-125 Separate session-trace token volume from model-call token usage in backend token accounting and frontend display.
- Completed: T-126 Add a local debug script `backend/scripts/print_knowledge_token_breakdown.py` that prints both token views plus per-stage retrieval breakdown for one fixed knowledge question.
- Next: T-127 Decide whether the next cheapest token reduction comes from trimming persisted retrieval-step payloads, limiting saved results per stage, or shrinking answer-time knowledge context.
## 2026-03-29 Thirty-Eighth Update
- Completed: T-127 Add a dedicated OpenDataLoader PDF integration module with preflight checks, batch conversion, mirrored derived outputs, and semantic JSON-driven chunk construction.
- Completed: T-128 Switch the default PDF ingestion path from the legacy page-based parser to OpenDataLoader while keeping `PDF_PARSER_BACKEND=legacy` as the explicit rollback path.
- Completed: T-129 Extend PDF chunk metadata and benchmark/debug outputs so original-PDF citations retain page/bbox/element information and parser/build stats become observable.
- Next: T-130 Re-run the targeted PDF benchmark gate once the Bailian embedding account is healthy again so a fresh `vector_ready=true` rebuild can confirm whether OpenDataLoader should remain the default parser.
## 2026-03-30 Thirty-Ninth Update
- Completed: T-130 Regroup OpenDataLoader PDF text chunks into section-aware neighborhoods so average PDF chunk length rises materially above the first migration baseline.
- Completed: T-131 Strengthen OpenDataLoader parent composition for grouped text, tables with nearby context, and figure-caption relationships without reverting to page-hard-split evidence.
- Completed: T-132 Rebuild the knowledge index with the new PDF chunk composition and rerun the targeted PDF benchmark slices plus compare / multi-hop / negation token profiling.
- Next: T-133 Investigate why fuzzy and cross-file PDF slices still regress under the new chunk composition even though compare grounding improves, and determine whether the next cheapest fix is retrieval-source preference or question-type routing rather than more chunk growth.
## 2026-03-30 Fortieth Update
- Completed: T-133 Add PDF family-overview recall plus question-type-specific family filtering so fuzzy and cross-file PDF questions pick the right report families before chunk competition.
- Completed: T-134 Add stronger source-type bias so PDF semantic/table evidence outranks `data_structure.md` and most legacy txt helpers in final evidence selection.
- Completed: T-135 Re-run the targeted PDF benchmark slices and capture final-evidence source-type/source-family diagnostics for the retrieval-side cleanup.
- Next: T-136 Fix compare / negation / multi-hop grounding against already-correct PDF evidence selection instead of continuing to tune parser hookup or chunk size.
