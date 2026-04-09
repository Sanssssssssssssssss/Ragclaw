# RUNBOOK

This is the operational entry document for this repo.

## What this repo contains

- [src/backend](/D:/GPT_Project/RAG_Model/src/backend): backend product code
- [src/frontend](/D:/GPT_Project/RAG_Model/src/frontend): frontend product code
- [backend/benchmarks](/D:/GPT_Project/RAG_Model/backend/benchmarks): benchmark runners and case files
- [backend/tests](/D:/GPT_Project/RAG_Model/backend/tests): backend tests
- [backend/scripts/dev](/D:/GPT_Project/RAG_Model/backend/scripts/dev): local startup and verification scripts
- [backend/storage](/D:/GPT_Project/RAG_Model/backend/storage): generated runtime and benchmark artifacts

## One-command startup

```powershell
.\backend\scripts\dev\start-dev.ps1 -InstallIfMissing
```

## Common startup commands

Full app:

```powershell
.\backend\scripts\dev\start-dev.ps1
```

Full app restart:

```powershell
.\backend\scripts\dev\start-dev.ps1 -Restart
```

Backend only:

```powershell
.\backend\scripts\dev\start-backend-dev.ps1 -Port 8015
```

Frontend only:

```powershell
.\backend\scripts\dev\start-frontend-dev.ps1 -ApiBaseUrl http://127.0.0.1:8015/api
```

CMD wrapper:

```cmd
.\backend\scripts\dev\start-dev.cmd
```

## Default local endpoints

- frontend: [http://127.0.0.1:3000](http://127.0.0.1:3000)
- backend: [http://127.0.0.1:8015](http://127.0.0.1:8015)
- health: [http://127.0.0.1:8015/health](http://127.0.0.1:8015/health)

## Environment variables

Do not commit real secrets.

Use [backend/.env.example](/D:/GPT_Project/RAG_Model/backend/.env.example). The main variables used by the project are:

- `LLM_PROVIDER`
- `LLM_MODEL`
- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_THINKING_TYPE`
- `ROUTER_MODEL`
- `ROUTER_API_KEY`
- `ROUTER_BASE_URL`
- `EMBEDDING_PROVIDER`
- `EMBEDDING_MODEL`
- `EMBEDDING_API_KEY`
- `EMBEDDING_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `ZHIPU_API_KEY`
- `ZHIPUAI_API_KEY`
- `ZHIPU_BASE_URL`
- `ZHIPU_MODEL`
- `BAILIAN_API_KEY`
- `DASHSCOPE_API_KEY`
- `BAILIAN_BASE_URL`
- `BAILIAN_MODEL`
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`
- `JUDGE_BASE_URL`
- `JUDGE_API_KEY`
- `JUDGE_MODEL`
- `JUDGE_TIMEOUT_SECONDS`
- `TAVILY_API_KEY`
- `TAVILY_PROJECT`

## Useful backend APIs

- `POST /api/chat`
- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/sessions/{session_id}/history`
- `GET /api/sessions/{session_id}/checkpoints`
- `POST /api/sessions/{session_id}/checkpoints/{checkpoint_id}/resume`
- `GET /api/sessions/{session_id}/hitl`
- `POST /api/sessions/{session_id}/hitl/{checkpoint_id}/decision`
- `GET /api/capabilities/mcp`
- `GET /api/context/sessions/{session_id}`
- `GET /api/context/memories`
- `GET /api/context/assemblies`
- `GET /api/knowledge/index/status`
- `POST /api/knowledge/index/rebuild`

## Focused verification commands

Backend compile:

```powershell
backend\.venv\Scripts\python.exe -m compileall src\backend
```

Frontend build:

```powershell
cd src/frontend
npm run build
```

Chat UI verification:

```powershell
.\backend\scripts\dev\run-chat-ui-verification.ps1
```

## Focused benchmark commands

Contract:

```powershell
backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_benchmark.py --suite contract
```

Integration:

```powershell
backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_benchmark.py --suite integration
```

Hard smoke:

```powershell
backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_benchmark.py --suite hard --limit 2
```

Live validation:

```powershell
backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_live_validation.py
```

## Current major system surfaces

- LangGraph orchestration
- durable checkpoint and resume
- HITL approve / reject / edit
- HITL audit trail
- minimal recovery
- Filesystem MCP + Web MCP
- context engine with working / episodic / semantic / procedural memory
- minimal frontend assets panel
