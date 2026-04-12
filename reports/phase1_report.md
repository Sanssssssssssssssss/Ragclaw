# Phase 1 Report: Infrastructure Abstractions and Local Parity

## Goal

Keep Ragclaw's local-first runtime behavior intact while introducing pluggable backend abstractions for sessions, run traces, queueing, and HITL persistence.

## Code Changes

- Added [src/backend/runtime/backends.py](/E:/GPTProject2/Ragclaw/src/backend/runtime/backends.py)
- Added [src/backend/runtime/hitl_repository.py](/E:/GPTProject2/Ragclaw/src/backend/runtime/hitl_repository.py)
- Updated [src/backend/runtime/runtime.py](/E:/GPTProject2/Ragclaw/src/backend/runtime/runtime.py)
- Updated [src/backend/runtime/agent_manager.py](/E:/GPTProject2/Ragclaw/src/backend/runtime/agent_manager.py)
- Updated [src/backend/runtime/session_manager.py](/E:/GPTProject2/Ragclaw/src/backend/runtime/session_manager.py)
- Updated [src/backend/runtime/policy.py](/E:/GPTProject2/Ragclaw/src/backend/runtime/policy.py)
- Updated [src/backend/observability/trace_store.py](/E:/GPTProject2/Ragclaw/src/backend/observability/trace_store.py)
- Added [backend/tests/test_runtime_backends.py](/E:/GPTProject2/Ragclaw/backend/tests/test_runtime_backends.py)
- Updated baseline-red tests in:
  - [backend/tests/test_agent_constraints.py](/E:/GPTProject2/Ragclaw/backend/tests/test_agent_constraints.py)
  - [backend/tests/test_checkpoint_api.py](/E:/GPTProject2/Ragclaw/backend/tests/test_checkpoint_api.py)
  - [backend/tests/test_harness_types.py](/E:/GPTProject2/Ragclaw/backend/tests/test_harness_types.py)
  - [backend/tests/test_knowledge_multiformat_indexing.py](/E:/GPTProject2/Ragclaw/backend/tests/test_knowledge_multiformat_indexing.py)

## Design Decisions

- `HarnessRuntime` now depends on repository/queue protocols rather than concrete local classes.
- Local mode remains the default through `build_runtime_backends(...)`.
- Compatibility aliases preserve current import paths:
  - `SessionManager -> FsSessionRepository`
  - `RunTraceStore -> JsonlRunTraceRepository`
  - `SessionSerialQueue -> InMemoryQueueBackend`
- HITL/checkpoint storage is wrapped behind `SqliteHitlRepository` instead of being configured ad hoc from `AgentManager`.
- Unsupported enterprise backend selections now fail fast through config validation instead of silently falling back.

## Commands Run

### Compile and focused tests

```powershell
.\backend\.venv\Scripts\python.exe -m compileall src/backend/runtime src/backend/observability backend/tests/test_runtime_backends.py
.\backend\.venv\Scripts\python.exe -m unittest backend.tests.test_runtime_backends backend.tests.test_harness_runtime backend.tests.test_harness_trace_store
.\backend\.venv\Scripts\python.exe -m unittest backend.tests.test_runtime_imports backend.tests.test_harness_chat_integration backend.tests.test_harness_adapters backend.tests.test_api_startup
.\backend\.venv\Scripts\python.exe -m unittest backend.tests.test_agent_constraints backend.tests.test_checkpoint_api backend.tests.test_harness_types backend.tests.test_knowledge_multiformat_indexing
.\backend\.venv\Scripts\python.exe -m compileall src/backend
```

All commands exited `0`.

### Full backend discovery

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover backend/tests
```

Exit code: `0`

- `198` tests ran in `135.077s`
- Result: `OK`
- Residual warnings:
  - Windows `ResourceWarning` for unclosed asyncio transports in live-validation-style tests
  - websocket deprecation warnings from upstream dependencies

Raw output: [artifacts/phase1/unittest_discover.txt](/E:/GPTProject2/Ragclaw/artifacts/phase1/unittest_discover.txt)

### Focused benchmark and live validation

```powershell
.\backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_benchmark.py --suite contract --deterministic-only --stub-decisions --limit 3 --output artifacts\phase1\harness_benchmark.json
.\backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_live_validation.py --limit 3 --output artifacts\phase1\live_validation.json
```

Both commands exited `0`.

Benchmark summary:

- total cases: `3`
- passed cases: `3`
- trace completeness: `1.0`
- tool/result reflection: `1.0`

Live validation summary:

- total cases: `3`
- passed cases: `3`
- session persistence integrity: `1.0`
- SSE order integrity: `1.0`
- trace completeness: `1.0`

Raw outputs:

- [artifacts/phase1/harness_benchmark.txt](/E:/GPTProject2/Ragclaw/artifacts/phase1/harness_benchmark.txt)
- [artifacts/phase1/harness_benchmark.json](/E:/GPTProject2/Ragclaw/artifacts/phase1/harness_benchmark.json)
- [artifacts/phase1/live_validation.txt](/E:/GPTProject2/Ragclaw/artifacts/phase1/live_validation.txt)
- [artifacts/phase1/live_validation.json](/E:/GPTProject2/Ragclaw/artifacts/phase1/live_validation.json)

## Done When Check

Phase 1 Done When required:

- local mode functionality remains equivalent: satisfied
- existing relevant tests pass: satisfied
- new interfaces have unit tests: satisfied
- behavior equivalence tests exist: satisfied through compatibility tests plus full discovery/baseline-red fixes
- no obvious benchmark regression: satisfied for focused smoke benchmark/live validation

## Residual Risks

- Queue semantics are still single-process only until Phase 2 lands.
- Session and trace persistence are still local-only until enterprise backends arrive.
- Resource cleanup warnings remain worth tightening before soak/chaos runs.

## Next Step

Move to Phase 2: Redis-backed distributed lease acquisition with TTL, heartbeat, idempotent release, and same-session single-active-run validation.
