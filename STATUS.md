# Ragclaw Infrastructure Upgrade Status

## Current Phase
Phase 5 closeout: merge-ready validation, capability-aware external drills, and honest blocked reporting

## Completed

- verified current runtime/session/trace/queue/API anchors
- identified current runtime composition path:
  - `AgentManager.get_harness_runtime()`
  - `build_harness_runtime(base_dir)`
- confirmed current local persistence landscape:
  - sessions: filesystem JSON
  - run traces: JSONL + summary JSON
  - HITL/checkpoints: local SQLite in `checkpointing.py`
  - queueing: in-process asyncio FIFO
- confirmed current API observability gaps:
  - `/health` exists
  - `/metrics` missing
  - CORS allow-all
- created initial execution plan and ADR skeletons
- captured baseline environment fingerprint
- ran baseline backend unittest discovery
- ran baseline harness benchmark smoke
- ran baseline live validation smoke
- ran local API function smoke and API load smoke
- wrote `reports/baseline_report.md`
- introduced runtime backend abstractions:
  - `SessionRepository`
  - `RunTraceRepository`
  - `QueueBackend`
  - `HitlRepository`
- converted local implementations into compatibility adapters:
  - `FsSessionRepository` with `SessionManager` alias
  - `JsonlRunTraceRepository` with `RunTraceStore` alias
  - `InMemoryQueueBackend` with `SessionSerialQueue` alias
  - `SqliteHitlRepository`
- refactored `build_harness_runtime(...)` and `AgentManager` initialization to compose through backend abstractions
- added `backend/tests/test_runtime_backends.py`
- fixed pre-existing red tests uncovered by baseline drift:
  - `backend.tests.test_agent_constraints`
  - `backend.tests.test_checkpoint_api`
  - `backend.tests.test_harness_types`
  - `backend.tests.test_knowledge_multiformat_indexing`
- reran full backend unittest discovery successfully
- reran harness benchmark smoke successfully after the abstraction refactor
- reran live validation smoke successfully after the abstraction refactor
- wrote `reports/phase1_report.md`
- implemented Redis-backed per-session leases with heartbeat and fencing tokens
- added focused Redis lease tests including a multi-process same-session serialization test
- implemented Postgres-backed run/event storage with migration `backend/migrations/0001_run_event_store.sql`
- implemented dual-write JSONL + Postgres trace persistence plus parity recording
- added runs explorer APIs:
  - `GET /api/runs`
  - `GET /api/runs/stats`
  - `GET /api/runs/{run_id}`
  - `GET /api/runs/{run_id}/events`
  - `GET /api/hitl/pending`
- added Prometheus metrics and `/metrics`
- connected metrics emission to canonical harness events
- added request-level HTTP tracing middleware
- enriched benchmark and live-validation outputs with execution metadata
- added docs and ops artifacts:
  - `docs/ops/runbook.md`
  - `docs/ops/observability.md`
  - `docs/ops/benchmarking.md`
  - `ops/grafana/ragclaw-observability-dashboard.json`
  - `ops/prometheus/ragclaw-alerts.yml`
- reran focused tests after adding HTTP tracing and benchmark metadata
- reran full backend unittest discovery successfully with Postgres-backed tests enabled
- reran harness benchmark smoke and live validation smoke with execution metadata persisted in the output JSON
- captured a direct dual-write parity smoke artifact
- captured a current real-startup API control-plane load smoke
- added infrastructure capability detection with `machine_capabilities.json` outputs
- added `PostgresSessionRepository` and wired `RAGCLAW_SESSION_BACKEND=postgres`
- added filesystem-to-Postgres session parity harness
- added external-infra drill harness with explicit blocked artifacts
- completed a real local Postgres transient disconnect + retry drill
- fixed dual-write duplicate-event retry handling so JSONL/Postgres parity stays intact after retry
- added a CI workflow entry for split local-first and external-infra verification
- wrote:
  - `reports/benchmark_report.md`
  - `reports/load_test_report.md`
  - `reports/chaos_report.md`
  - `reports/final_implementation_report.md`

## In Progress

- review final diff and isolate commit boundaries that keep unrelated workspace changes out of scope

## Known Risks / Blockers

- real Redis restart drills still need a real Redis server or Docker; this workspace has neither, so those drills now emit blocked artifacts instead of being silently absent
- benchmark and live validation may depend on model/provider credentials and network reachability
- soak duration may need scaled-down execution depending on machine capacity
- full unittest discovery is now green, but there are still non-failing warnings to keep in view:
  - Windows `ResourceWarning` for unclosed asyncio transports in some live validation paths
  - sqlite/tempfile cleanup needs to stay tidy while we add more backends
- Postgres validation currently depends on the local binary-bootstrapped server on `127.0.0.1:35432`
- the remaining chaos gaps are Redis external drills, remote CI execution, and longer external soak, not core runtime wiring

## Next Steps

1. review the remaining diff and isolate unrelated user workspace changes from the infrastructure branch scope
2. dry-review the new CI workflow locally and leave remote execution to the first CI-capable environment
3. commit the completed infrastructure/observability closeout with the current honest blocked-gap notes
