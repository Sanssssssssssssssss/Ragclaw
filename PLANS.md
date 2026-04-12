# Ragclaw Infrastructure + Observability Upgrade Plan

## Scope
Upgrade Ragclaw from a local-first single-node agent runtime into a pluggable infrastructure-oriented system without changing canonical harness control semantics.

Primary themes:

- persistence and extensibility
- observability / LLMOps

Out of primary scope unless required for compatibility:

- major RAG algorithm rewrites
- query routing redesign
- reranking strategy changes
- new agent framework

## Current Anchors Verified On 2026-04-11

- `src/backend/runtime/runtime.py`
  - `build_harness_runtime(...)` currently wires `RunTraceStore` and `SessionSerialQueue` into `HarnessRuntime`
- `src/backend/runtime/session_manager.py`
  - `SessionManager` is filesystem-backed and persists session JSON under `backend/sessions`
- `src/backend/observability/trace_store.py`
  - `RunTraceStore` persists append-only JSONL traces plus summary JSON
- `src/backend/runtime/policy.py`
  - `SessionSerialQueue` is an in-process asyncio per-session FIFO queue
- `src/backend/api/app.py`
  - `/health` exists
  - `/metrics` is now available
  - CORS is currently configured as allow-all

## Drift / Reality Notes

- HITL and checkpoint persistence already have a local SQLite-backed implementation in `src/backend/orchestration/checkpointing.py`. This should become the seed local `HitlRepository` rather than be re-invented.
- Observability already has partial OTel tracing from the previous phase, but metrics and repository/query infrastructure are still missing.
- There is already a meaningful benchmark/live-validation harness under `backend/benchmarks`, so the upgrade should extend and connect it rather than replace it.
- Redis queue, Postgres run/event storage, and `/metrics` are now implemented behind configuration-selected backends.
- The repo currently appears to use `unittest`-style execution rather than a full pytest-native toolchain, so new validation entrypoints should stay compatible with the existing setup.

## Phase Plan

### Phase 0: Audit + Baseline
Done when:

- relevant runtime, API, observability, benchmark, and test entrypoints are confirmed
- environment fingerprint is captured
- baseline tests, benchmark smoke, and service smoke are run
- outputs are stored under `artifacts/baseline/`
- baseline summary exists at `reports/baseline_report.md`

Risks:

- live benchmark paths may depend on local API keys or model connectivity
- baseline may need scaled-down variants if environment capacity is limited

### Phase 1: Infrastructure Abstractions + Local Parity
Deliverables:

- repository/backend interfaces:
  - `SessionRepository`
  - `RunTraceRepository`
  - `QueueBackend`
  - `HitlRepository`
- local adapters:
  - filesystem session repository
  - JSONL trace repository
  - in-memory queue backend
  - SQLite HITL repository
- runtime wiring updated to depend on abstractions
- compatibility tests and behavior parity tests

Done when:

- local mode remains default
- local mode behavior matches pre-refactor semantics
- runtime no longer directly depends on concrete local backends
- touched modules have focused tests and parity checks

Risks:

- refactor could accidentally alter ordering or lifecycle semantics
- agent manager/session code may need careful compatibility shims

### Phase 2: Redis Queue Backend + Distributed Lease
Deliverables:

- Redis-backed `QueueBackend`
- per-session lease with TTL and heartbeat
- wait-time stats and queue depth metrics
- multi-worker integration tests and failure injection

Done when:

- same-session double-active count is 0 under concurrent tests
- lease heartbeat and expiry are validated
- local in-memory queue remains available and default

Risks:

- local environment may not already have Redis; likely use Docker/testcontainers or a compose profile
- async shutdown and lease fencing semantics need careful testing

### Phase 3: Postgres Event Store + Dual Write
Deliverables:

- Postgres schema + migrations
- Postgres implementations for run/session/HITL persistence where appropriate
- dual-write JSONL + Postgres event persistence
- parity checker and import/migration tooling
- run explorer APIs

Done when:

- Postgres path runs end-to-end
- parity mismatch count is 0 in tests and load runs
- run explorer APIs return DB-backed results

Risks:

- idempotency, ordering, and crash-retry semantics must be explicit
- local compatibility/migration needs careful handling

### Phase 4: Observability + LLMOps
Deliverables:

- Prometheus metrics and `/metrics`
- richer runtime/API OTel spans and attributes
- optional OTLP export configuration
- Grafana dashboard JSON
- Prometheus alert rules
- benchmark/run metadata correlation

Done when:

- metrics are scrapeable and change under test load
- traces show request -> run -> tool/retrieval/checkpoint/HITL -> answer
- dashboards/alerts are deliverable artifacts, not placeholders

Risks:

- metrics can drift from canonical event semantics if instrumented in the wrong place

### Phase 5: Verification, Load, Soak, Chaos
Deliverables:

- repeatable benchmark harness
- load/stress/soak scripts
- failure injection scripts
- coverage artifact for touched modules
- final reports

Done when:

- smoke/load/stress/soak variants are actually run
- failure drills have measured outcomes
- final reports include throughput, latency, queueing, parity, and error metrics

Risks:

- hardware/time limits may require scaled-down runs
- external dependency startup may be unavailable in the environment and require documented fallback

## Validation Strategy

- baseline before major code changes
- phase-by-phase unit, integration, and perf smoke
- dual-write parity checks after Postgres work lands
- observability smoke after metrics/tracing work lands
- final regression with existing critical tests

## Success Criteria

- local-first compatibility preserved
- pluggable backends wired by configuration
- distributed same-session serialization proven
- queryable run/event warehouse available
- `/metrics` and OTel exports operational
- reproducible benchmark and chaos reports committed
