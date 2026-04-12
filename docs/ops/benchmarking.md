# Ragclaw Benchmarking And Validation

## Benchmark Principles

- baseline first
- compare against the same Git SHA and backend config
- keep raw JSON outputs
- keep human-readable markdown summaries
- never silently skip missing infrastructure

## Current Repeatable Entry Points

Harness benchmark:

```powershell
.\backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_benchmark.py --suite contract --deterministic-only --stub-decisions --limit 3
```

Live validation:

```powershell
.\backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_live_validation.py --limit 3
```

Infrastructure runtime matrix:

```powershell
.\backend\.venv\Scripts\python.exe backend\benchmarks\run_infra_runtime_matrix.py --mode local-only --output artifacts\closeout\latest\infra_runtime_matrix.json --load-runs 12 --load-concurrency 4 --same-session-runs 8 --same-session-concurrency 2 --soak-seconds 10 --soak-concurrency 4 --include-dualwrite --include-redis --postgres-dsn postgresql://postgres@127.0.0.1:35432/postgres
```

External infrastructure matrix:

```powershell
.\backend\.venv\Scripts\python.exe backend\benchmarks\run_external_infra_matrix.py --mode direct --allow-local-postgres-restart --postgres-dsn postgresql://postgres@127.0.0.1:35432/postgres --output artifacts\closeout\latest\external_infra_matrix.json
```

Session repository parity:

```powershell
.\backend\.venv\Scripts\python.exe backend\benchmarks\run_session_repository_parity.py --postgres-dsn postgresql://postgres@127.0.0.1:35432/postgres --output artifacts\closeout\latest\session_repository_parity.json
```

Observability validation bundle:

```powershell
.\backend\scripts\dev\validate-observability.ps1
```

## Execution Metadata

Benchmark outputs include `execution_metadata` with:

- capture time
- Git SHA
- Python version
- platform
- working directory
- selected backend and observability environment variables
- benchmark selection config

Infrastructure outputs also include:

- `machine_capabilities.json`
- explicit drill `blocked_reason` entries
- local-only vs external-infra mode selection

## Current Validation Coverage

Automated validation now covers:

- local runtime behavior after abstraction
- Redis lease semantics in multi-process tests through `fakeredis`
- Postgres run/event store roundtrip
- Postgres session repository roundtrip and filesystem import parity
- JSONL/Postgres dual-write parity, including duplicate-event retry suppression
- metrics scrape path
- OTel span emission
- benchmark/live-validation smoke
- repo-native infrastructure matrix covering local load, same-session contention, dual-write parity, Redis two-process contention, scaled soak, and session CRUD roundtrips
- external Postgres transient disconnect + retry drill with real stop/start on the local bootstrapped Postgres

## Machine Capability Detection

`backend/benchmarks/infra_capabilities.py` is now the single capability detector for infra-focused harnesses.

It records:

- Docker availability
- `redis-server` availability
- Postgres DSN reachability
- Python dependency imports
- multiprocessing spawn support
- local Postgres start/stop script availability

Drill scripts must now either:

- run the requested drill, or
- emit a blocked artifact with the specific missing prerequisite

## Local Result Snapshot

From `artifacts/closeout/20260411T220107`:

- `machine_capabilities.json`
  - Docker: unavailable
  - `redis-server`: unavailable
  - Postgres DSN: reachable
  - external-infra mode: available for direct Postgres only
- `infra_runtime_matrix.json`
  - local many-sessions throughput: `28.087 runs/s`
  - same-session contention throughput: `12.686 runs/s`
  - dual-write parity mismatches: `0`
  - local soak: `407` completed runs / `10s` / `0` failures
  - session CRUD roundtrip:
    - filesystem: `32.29 ms`
    - postgres: `374.62 ms`
- `external_infra_matrix.json`
  - Postgres retry drill: passed
  - Redis drills: blocked locally with explicit reason
- `session_repository_parity.json`
  - status: passed
  - mismatches: `[]`

## CI Split

Workflow:

- `.github/workflows/infra-observability-closeout.yml`

Intended verification split:

- local-first Windows job
- external Linux job with Postgres parity and external-infra matrix

This workflow has been added and dry-reviewed locally, but it has not been executed from GitHub Actions as part of this local closeout.

## Remaining Honest Gaps

The following still depend on environment availability:

- real Redis restart drills against an external Redis server
- full external Redis matrix in a machine that actually has Docker or `redis-server`
- longer-duration external soak runs under dependency interruption

When these are unavailable, keep the blocked artifact and report it explicitly instead of inventing coverage.
