# Ragclaw

Ragclaw is a local-first agent and RAG workbench with:

- `src/backend` for runtime, orchestration, capabilities, context, API
- `src/frontend` for the UI
- `backend/benchmarks`, `backend/tests`, `backend/storage`, `backend/scripts` as support directories

Start here:

- [QUICKSTART.md](QUICKSTART.md)
- [RUNBOOK.md](RUNBOOK.md)
- [CODEX_HANDOFF.md](CODEX_HANDOFF.md)
- [docs/ops/runbook.md](docs/ops/runbook.md)
- [docs/ops/observability.md](docs/ops/observability.md)
- [docs/ops/benchmarking.md](docs/ops/benchmarking.md)

Environment setup:

- copy [backend/.env.example](backend/.env.example) to `backend/.env`
- fill in your own keys locally

One-command local start:

```powershell
.\backend\scripts\dev\start-dev.ps1 -InstallIfMissing
```

Default URLs:

- Frontend: [http://127.0.0.1:3000](http://127.0.0.1:3000)
- Backend: [http://127.0.0.1:8015](http://127.0.0.1:8015)
- Health: [http://127.0.0.1:8015/health](http://127.0.0.1:8015/health)
- Metrics: [http://127.0.0.1:8015/metrics](http://127.0.0.1:8015/metrics)

LangSmith Studio and OTel:

- The real LangGraph orchestration graph is exposed through [langgraph.json](E:\GPTProject2\Ragclaw\langgraph.json) and [studio_entry.py](E:\GPTProject2\Ragclaw\src\backend\orchestration\studio_entry.py).
- Add `LANGSMITH_API_KEY` to `backend/.env` before opening Studio. If you already keep a legacy `LANGCHAIN_API_KEY`, the Studio startup script will reuse it automatically.
- Studio runs now default into a dedicated LangSmith project named `Ragclaw Studio`. Override it with `RAGCLAW_STUDIO_LANGSMITH_PROJECT` in `backend/.env` or `-ProjectName` on the startup script.
- Start local Studio development mode with `.\backend\scripts\dev\start-langgraph-studio.ps1 -Mode dev -NoBrowser`
- Optional production-like local validation uses `.\backend\scripts\dev\start-langgraph-studio.ps1 -Mode up`
- Enable console OTel while running Studio with `.\backend\scripts\dev\start-langgraph-studio.ps1 -Mode dev -EnableConsoleTracing`
- Send spans to OTLP with `.\backend\scripts\dev\start-langgraph-studio.ps1 -Mode dev -OtlpEndpoint http://127.0.0.1:4318/v1/traces`
- The Studio dev script adds `--allow-blocking` by default because LangGraph's local blocking detector can flag harmless synchronous calls from third-party startup code like `os.getcwd`. Use `-StrictNonBlocking` only when you want to audit and chase those warnings.
- Minimal Studio input can be as small as `{"user_message":"hello","history":[]}`; `run_id`, `thread_id`, and runtime bindings are synthesized from the current harness-backed graph.
- To inspect the whole system graph in Studio: open the `ragclaw` assistant, go to the graph view to see the node topology, then open a thread/run to inspect node-level state, tool calls, checkpoints, and time-travel state. `Threads` shows persisted thread state, `Runs` shows each execution, and drilling into a node reveals the current graph state plus the context/call traces we already persist.
- Minimal OTel is off unless enabled with `RAGCLAW_OTEL_ENABLED=1`; send spans to stdout with `RAGCLAW_OTEL_CONSOLE_EXPORTER=1` or to OTLP with `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces`

Focused validation:

- Run the observability-focused validation bundle with `.\backend\scripts\dev\validate-observability.ps1`
- Run the repo-native local-only matrix with `.\backend\.venv\Scripts\python.exe backend\benchmarks\run_infra_runtime_matrix.py --mode local-only --output artifacts\closeout\latest\infra_runtime_matrix.json --load-runs 12 --load-concurrency 4 --same-session-runs 8 --same-session-concurrency 2 --soak-seconds 10 --soak-concurrency 4 --include-dualwrite --include-redis --postgres-dsn postgresql://postgres@127.0.0.1:35432/postgres`
- Run the external-infra drill harness with `.\backend\.venv\Scripts\python.exe backend\benchmarks\run_external_infra_matrix.py --mode direct --allow-local-postgres-restart --postgres-dsn postgresql://postgres@127.0.0.1:35432/postgres --output artifacts\closeout\latest\external_infra_matrix.json`
- Run filesystem-to-Postgres session parity with `.\backend\.venv\Scripts\python.exe backend\benchmarks\run_session_repository_parity.py --postgres-dsn postgresql://postgres@127.0.0.1:35432/postgres --output artifacts\closeout\latest\session_repository_parity.json`
- The infra harnesses now always emit `machine_capabilities.json`; if Docker / Redis / Postgres controls are missing, they leave blocked artifacts instead of silently skipping drills
- CI workflow for the split local/external matrix lives at `.github/workflows/infra-observability-closeout.yml`
- VS Code also exposes `Studio: LangGraph dev`, `Studio: LangGraph dev (Console tracing)`, and `Observability: Focused validation`
