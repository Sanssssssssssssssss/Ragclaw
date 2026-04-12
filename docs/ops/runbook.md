# Ragclaw Runtime Runbook

## Goals

- keep the default developer experience local-first
- allow optional Redis and Postgres backends without changing `/api/chat` semantics
- make runtime state, traces, and drills explicit instead of silently skipping missing infrastructure

## Default Local Mode

Local mode remains the default when no backend overrides are set.

Key defaults:

- session backend: filesystem JSON
- trace backend: JSONL under `backend/storage/runs`
- queue backend: in-memory per-session FIFO
- HITL backend: local SQLite

Startup:

```powershell
.\backend\scripts\dev\start-dev.ps1 -InstallIfMissing
```

Health endpoints:

- `GET /health`
- `GET /metrics`

## Backend Selection

Environment variables:

- `RAGCLAW_SESSION_BACKEND=filesystem|postgres`
- `RAGCLAW_TRACE_BACKEND=jsonl|postgres|dualwrite`
- `RAGCLAW_QUEUE_BACKEND=inmemory|redis`
- `RAGCLAW_HITL_BACKEND=sqlite`
- `RAGCLAW_POSTGRES_DSN=postgresql://postgres@127.0.0.1:35432/postgres`
- `RAGCLAW_REDIS_URL=redis://127.0.0.1:6379/0`
- `RAGCLAW_QUEUE_NAMESPACE=ragclaw`
- `RAGCLAW_QUEUE_LEASE_TTL_SECONDS=30`
- `RAGCLAW_QUEUE_HEARTBEAT_INTERVAL_SECONDS=10`
- `RAGCLAW_QUEUE_POLL_INTERVAL_SECONDS=0.25`

Recommended rollout order:

1. local filesystem sessions + JSONL traces + in-memory queue
2. dual-write JSONL + Postgres traces
3. Postgres session repository
4. Redis queue for multi-worker session serialization

## Session Storage Modes

### Filesystem

Best for:

- default local development
- zero external dependencies
- easy manual inspection and rollback

Notes:

- sessions live under `backend/sessions/*.json`
- archived session files move under `backend/sessions/archive/`

### Postgres SessionRepository

Best for:

- multi-process persistence beyond one workspace
- parity checks against filesystem sessions
- import/migration away from local JSON session files

Validated commands:

```powershell
.\backend\.venv\Scripts\python.exe backend\benchmarks\run_session_repository_parity.py --postgres-dsn postgresql://postgres@127.0.0.1:35432/postgres --output artifacts\closeout\latest\session_repository_parity.json
```

This script imports a filesystem-backed temp session into Postgres, compares normalized session history, compressed context, exclusion metadata, and agent-visible history, and emits a passed/failed parity artifact.

## Trace Storage Modes

### JSONL

Best for:

- local development
- easy filesystem inspection
- fallback when Postgres is unavailable

Artifacts:

- `run-*.jsonl`
- `run-*.summary.json`

### Postgres

Best for:

- query APIs
- operational audit
- run/event filtering and stats

Schema:

- `sessions`
- `session_messages`
- `runs`
- `run_events`
- `hitl_requests`
- `hitl_decisions`
- `run_trace_parity`

### Dual-write

Recommended transition mode.

Benefits:

- preserves local JSONL durability
- populates Postgres explorer APIs
- records parity checksums and event counts
- now tolerates duplicate retry of the same `event_id` without duplicating JSONL rows

## Queue Backends

### In-memory queue

Use for:

- default local mode
- single-process development
- minimal dependency footprint

### Redis queue

Use for:

- multi-process session serialization
- per-session TTL-based leases with heartbeat
- queue wait measurement

Failure modes to watch:

- Redis unavailable during acquire
- lease lost because heartbeat misses the TTL window
- duplicate release

Current status in this workspace:

- multi-process serialization is covered through `fakeredis`
- real Redis restart/expiry drills are still blocked locally because neither Docker nor `redis-server` exists on this machine

## Local And External Validation Modes

### Local-only mode

Use this when you want repeatable repo-native validation without external dependencies:

```powershell
.\backend\.venv\Scripts\python.exe backend\benchmarks\run_infra_runtime_matrix.py --mode local-only --output artifacts\closeout\latest\infra_runtime_matrix.json --load-runs 12 --load-concurrency 4 --same-session-runs 8 --same-session-concurrency 2 --soak-seconds 10 --soak-concurrency 4 --include-dualwrite --include-redis --postgres-dsn postgresql://postgres@127.0.0.1:35432/postgres
```

Outputs now include:

- `machine_capabilities.json`
- scenario metrics
- session repository CRUD roundtrip timings
- blocked reasons when optional scenarios are unavailable

### External-infra mode

Use this when Docker or directly managed Redis/Postgres services are available:

```powershell
.\backend\.venv\Scripts\python.exe backend\benchmarks\run_external_infra_matrix.py --mode direct --allow-local-postgres-restart --postgres-dsn postgresql://postgres@127.0.0.1:35432/postgres --output artifacts\closeout\latest\external_infra_matrix.json
```

The external matrix:

- writes `machine_capabilities.json`
- runs real drills when the machine supports them
- emits blocked artifacts instead of silently skipping unsupported drills
- writes `postgres_retry_drill.json` when the Postgres retry drill runs

Current local result:

- Postgres transient disconnect + retry drill: passed
- Redis restart drill: blocked because no Docker and no `redis-server`

## Local Postgres

This repo includes helper scripts for a no-Docker local Postgres path on Windows.

Bootstrap binaries:

```powershell
.\backend\scripts\dev\bootstrap-postgres-binaries.ps1
```

Start local Postgres:

```powershell
.\backend\scripts\dev\start-postgres-local.ps1
```

Stop local Postgres:

```powershell
.\backend\scripts\dev\stop-postgres-local.ps1
```

Notes:

- the validated local port in this workspace is `35432`
- startup logs are written to `artifacts/postgres_logs/postgres-local.stdout.log` and `postgres-local.stderr.log`
- the stop script now uses a non-reserved PowerShell variable name, so it works under automated restart drills

## Query APIs

Run explorer endpoints:

- `GET /api/runs`
- `GET /api/runs/stats`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/events`
- `GET /api/hitl/pending`

Session endpoints now support:

- create
- rename
- archive
- delete
- message/history reads

## CI

Repository workflow:

- `.github/workflows/infra-observability-closeout.yml`

Intended split:

- Windows local-first job: compile, full unittest discovery, observability validation, harness smoke
- Linux external job: Postgres parity tests plus external-infra matrix when Docker is available

This workflow was reviewed locally in this workspace, but not executed from the remote runner during this closeout pass.

## Rollback

If Redis or Postgres introduce instability:

1. unset `RAGCLAW_REDIS_URL`
2. set `RAGCLAW_QUEUE_BACKEND=inmemory`
3. set `RAGCLAW_TRACE_BACKEND=jsonl`
4. set `RAGCLAW_SESSION_BACKEND=filesystem`
5. restart the backend

This returns the system to the original local-first control path.
