# Ragclaw

Ragclaw is a local-first agent and RAG workbench with:

- `src/backend` for runtime, orchestration, capabilities, context, API
- `src/frontend` for the UI
- `backend/benchmarks`, `backend/tests`, `backend/storage`, `backend/scripts` as support directories

Start here:

- [QUICKSTART.md](/D:/GPT_Project/RAG_Model/QUICKSTART.md)
- [RUNBOOK.md](/D:/GPT_Project/RAG_Model/RUNBOOK.md)
- [CODEX_HANDOFF.md](/D:/GPT_Project/RAG_Model/CODEX_HANDOFF.md)

Environment setup:

- copy [backend/.env.example](/D:/GPT_Project/RAG_Model/backend/.env.example) to `backend/.env`
- fill in your own keys locally

One-command local start:

```powershell
.\backend\scripts\dev\start-dev.ps1 -InstallIfMissing
```

Default URLs:

- Frontend: [http://127.0.0.1:3000](http://127.0.0.1:3000)
- Backend: [http://127.0.0.1:8015](http://127.0.0.1:8015)
- Health: [http://127.0.0.1:8015/health](http://127.0.0.1:8015/health)

LangSmith Studio and OTel:

- The real LangGraph orchestration graph is exposed through [langgraph.json](E:\GPTProject2\Ragclaw\langgraph.json) and [studio_entry.py](E:\GPTProject2\Ragclaw\src\backend\orchestration\studio_entry.py).
- Start local Studio development mode with `.\backend\scripts\dev\start-langgraph-studio.ps1 -Mode dev -NoBrowser`
- Optional production-like local validation uses `.\backend\scripts\dev\start-langgraph-studio.ps1 -Mode up`
- Minimal Studio input can be as small as `{"user_message":"hello","history":[]}`; `run_id`, `thread_id`, and runtime bindings are synthesized from the current harness-backed graph.
- Minimal OTel is off unless enabled with `RAGCLAW_OTEL_ENABLED=1`; send spans to stdout with `RAGCLAW_OTEL_CONSOLE_EXPORTER=1` or to OTLP with `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces`
