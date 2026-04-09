# CODEX_HANDOFF

This note is for the next Codex working on this repository.

## First rule

Do not assume the repo is a simple chatbot app.

Read the repository before proposing changes:

- [README.md](/D:/GPT_Project/RAG_Model/README.md)
- [RUNBOOK.md](/D:/GPT_Project/RAG_Model/RUNBOOK.md)
- [src/backend](/D:/GPT_Project/RAG_Model/src/backend)
- [src/frontend](/D:/GPT_Project/RAG_Model/src/frontend)

If you skip that, you will probably reintroduce duplicate execution paths or bypass the current architecture.

## What we are building

Ragclaw is a local-first agent and RAG workbench.

The current system already has:

- unified capability system
- LangGraph orchestration
- durable checkpoint / resume
- HITL approve / reject / edit
- HITL audit trail
- minimal recovery
- Filesystem MCP + Web MCP
- end-to-end context management

## The architecture we are protecting

- `HarnessRuntime` is still the lifecycle owner
- LangGraph is orchestration, not control plane
- canonical harness events are the execution truth
- capability system is the only capability layer
- context assembly is the only prompt-context gate

Do not add a second runtime.
Do not bypass context assembly.
Do not push raw trace, audit, or checkpoint dumps into prompts.

## Where to look first

- orchestration: [src/backend/orchestration](/D:/GPT_Project/RAG_Model/src/backend/orchestration)
- capabilities: [src/backend/capabilities](/D:/GPT_Project/RAG_Model/src/backend/capabilities)
- context: [src/backend/context](/D:/GPT_Project/RAG_Model/src/backend/context)
- runtime: [src/backend/runtime](/D:/GPT_Project/RAG_Model/src/backend/runtime)
- API: [src/backend/api](/D:/GPT_Project/RAG_Model/src/backend/api)
- frontend workbench: [src/frontend/src/components/chat](/D:/GPT_Project/RAG_Model/src/frontend/src/components/chat)

## Operational truth

Before changing behavior:

1. inspect the current backend path
2. inspect the current frontend state/store path
3. prefer focused validation over broad rewrites
4. preserve API, SSE, session, and canonical event semantics unless the task explicitly changes them

## Local run

If dependencies are not installed yet:

```powershell
.\backend\scripts\dev\start-dev.ps1 -InstallIfMissing
```

If they are already installed:

```powershell
.\backend\scripts\dev\start-dev.ps1
```

## Secrets

Use your own local `backend/.env`.
Do not print or commit real keys.
Use [backend/.env.example](/D:/GPT_Project/RAG_Model/backend/.env.example) for variable names.
