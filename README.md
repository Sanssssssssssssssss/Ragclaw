# Ragclaw

Ragclaw is a local-first RAG and agent workbench built for iterative research, transparent retrieval, and editable long-term context.

This repository started from the ideas in `Skill-First-Hybrid-RAG`, but it is no longer a simple mirror or light fork. The retrieval stack, benchmark framework, runtime controls, knowledge ingestion pipeline, and project-memory workflow have all been expanded for an ongoing private-to-public research build.

## Project Status

- `retrieval-path-cleanup`
- `grounding-eval-in-progress`

Current direction:

- consolidate indexed knowledge QA onto the formal retrieval path
- keep Markdown as the persistent project memory layer
- make RAG behavior easier to inspect, benchmark, and improve over time
- keep the system practical for local development on Windows and Linux

## What This Repo Is Now

Ragclaw is not just a chat demo. It is a working environment for:

- local chat and agent experimentation
- indexed knowledge retrieval across `md`, `json`, `txt`, `pdf`, and `xlsx`
- transparent retrieval traces and tool traces
- editable memory, skills, and workspace files
- modular backend benchmarks for routing, retrieval, grounding, and tool use

The repo is intentionally opinionated:

- files are treated as inspectable state, not hidden internal memory
- project context is rebuilt from Markdown memory files
- retrieval quality should be measurable without being silently repaired by tool backreads
- benchmarks should be cheap to slice and rerun by module, subtype, modality, and question type

## Current Retrieval Direction

For indexed knowledge questions, the main path is being tightened toward formal retrieval:

- vector retrieval
- BM25 retrieval
- fused evidence

The goal is to make indexed knowledge QA reflect real retrieval quality, instead of relying on skill or generic file-reading tools as the primary answer path.

This means benchmark scores may become more honest:

- weak retrieval is surfaced as weak retrieval
- partial evidence is surfaced as partial evidence
- groundedness gaps are easier to see and fix

## Core Capabilities

- FastAPI backend with SSE streaming
- Next.js frontend workbench
- session persistence to local JSON
- editable Markdown memory
- skill files stored as readable `SKILL.md`
- formal knowledge indexing for:
  - `md`
  - `json`
  - `txt`
  - text-extractable `pdf`
  - structured `xlsx`
- configurable embedding and LLM providers
- modular backend benchmark runner with optional judge-model scoring

## Repository Layout

```text
backend/
  api/                   FastAPI routes
  benchmarks/            modular benchmark runner, evaluators, judge hooks, cases
  graph/                 agent logic, routing, prompts, sessions, memory handling
  knowledge/             local knowledge corpus
  knowledge_retrieval/   ingestion, indexing, vector/BM25 retrieval, orchestration
  memory/                persistent Markdown memory
  scripts/               backend validation and maintenance scripts
  sessions/              persisted chat sessions
  skills/                local skill specs
  storage/               derived benchmark and index artifacts
  tools/                 terminal, python, file, and related tools
frontend/
  src/                   UI app, state, components
scripts/
  dev/                   one-shot startup and benchmark scripts
```

## Knowledge Indexing Scope

The current indexed file scope for `backend/knowledge/` is:

- `md`
- `json`
- `txt`
- `pdf` with directly extractable text
- `xlsx`

Current non-goals:

- OCR-heavy PDF workflows
- long-horizon autonomous task benchmarks
- pretending partially indexed content is fully grounded

## Benchmarks

The backend benchmark framework supports:

- `smoke` and `full` suites
- module-level runs such as:
  - `rag`
  - `routing`
  - `tool`
  - `constraints`
  - `groundedness`
- RAG subtype runs such as:
  - `retrieval`
  - `grounding`
  - `ranking` scaffold
  - `table` scaffold

RAG benchmark cases are maintained as hand-editable JSON files under:

- `backend/benchmarks/rag/retrieval_cases.json`
- `backend/benchmarks/rag/grounding_cases.json`
- `backend/benchmarks/rag/ranking_cases.json`
- `backend/benchmarks/rag/table_cases.json`

Typical usage:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev\run-backend-benchmarks.ps1 -Module rag -RagSubtype retrieval -SamplePerType 2 -Port 8015
```

## Local Development

Start the full app:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-dev.ps1
```

Restart the full app:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-dev.ps1 -Restart
```

Backend only:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev\start-backend-dev.ps1 -Port 8015
```

## Configuration

Configuration is primarily loaded from:

- `backend/.env`
- `backend/config.py`

The repository currently supports multiple providers for LLMs and embeddings. The active local setup can differ from the example file, depending on the machine and current experiment.

## Why This Repo Exists

This project is being shaped into a practical RAG research surface where we can:

- change retrieval behavior without guessing what happened
- inspect traces instead of trusting black-box outputs
- compare groundedness and benchmark deltas across iterations
- keep evolving the repo without being trapped by upstream assumptions

## Acknowledgements

This repository builds on ideas and early structure from the open-source project:

- `lyxhnu/Skill-First-Hybrid-RAG`

Thanks to the original author for publishing a useful starting point for transparent hybrid-RAG experimentation.

Ragclaw has since diverged substantially in scope and implementation, and it will continue evolving as its own project.
