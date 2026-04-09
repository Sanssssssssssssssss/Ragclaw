# QUICKSTART

If you just downloaded this repo and want it running with the fewest steps, do this.

## 1. Configure local keys

Copy:

```powershell
Copy-Item .\backend\.env.example .\backend\.env
```

Then edit `backend/.env` and fill in your own provider keys.

Use [backend/.env.example](/D:/GPT_Project/RAG_Model/backend/.env.example) as the source of truth for required environment variable names.

## 2. Run once

From the repo root:

```powershell
.\backend\scripts\dev\start-dev.ps1 -InstallIfMissing
```

That script will:

- create `backend/.venv` if missing
- install backend dependencies if missing
- install frontend dependencies if missing
- start backend on `8015`
- start frontend on `3000`

## 3. Open the app

- Frontend: [http://127.0.0.1:3000](http://127.0.0.1:3000)
- Backend health: [http://127.0.0.1:8015/health](http://127.0.0.1:8015/health)

## If you are lost

- full command reference: [RUNBOOK.md](/D:/GPT_Project/RAG_Model/RUNBOOK.md)
- Codex handoff: [CODEX_HANDOFF.md](/D:/GPT_Project/RAG_Model/CODEX_HANDOFF.md)
- local setup details: [LOCAL_DEV.md](/D:/GPT_Project/RAG_Model/LOCAL_DEV.md)
