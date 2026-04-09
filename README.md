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
