# Baseline Report

## Environment Fingerprint

See [environment_fingerprint.json](/E:/GPTProject2/Ragclaw/artifacts/baseline/environment_fingerprint.json).

- OS: Microsoft Windows 11 ????? 10.0.26200
- CPU: Intel(R) Core(TM) Ultra 9 185H
- Cores: 16 physical / 22 logical
- RAM: 33,736,093,696 bytes
- Python: 3.13.12
- Git SHA: 7bca2e8f9a804638a272aa1fbdb46a22b646a3f4
- Timestamp (UTC): 2026-04-11T18:08:49.4639625Z

## Baseline Commands

1. Environment fingerprint
   - Initial attempt failed: inline Python used `psutil`, but `psutil` is not installed in the backend venv.
   - Recovery: switched to PowerShell + CIM-based collection and wrote the same artifact successfully.

2. Full backend unittest discovery
   - Command: `backend\.venv\Scripts\python.exe -m unittest discover backend/tests`
   - Exit code: 1
   - Raw output: [unittest_discover.txt](/E:/GPTProject2/Ragclaw/artifacts/baseline/unittest_discover.txt)
   - Result summary: 193 tests ran in 129.591s, with 4 failures and 5 errors.

3. Harness benchmark smoke
   - Command: `backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_benchmark.py --suite contract --deterministic-only --stub-decisions --limit 3 --output artifacts\baseline\harness_benchmark.json`
   - Exit code: 0
   - Raw output: [harness_benchmark_smoke.txt](/E:/GPTProject2/Ragclaw/artifacts/baseline/harness_benchmark_smoke.txt)
   - JSON artifact: [harness_benchmark.json](/E:/GPTProject2/Ragclaw/artifacts/baseline/harness_benchmark.json)

4. Live validation smoke
   - Command: `backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_live_validation.py --limit 3 --output artifacts\baseline\live_validation.json`
   - Exit code: 0
   - Raw output: [live_validation_smoke.txt](/E:/GPTProject2/Ragclaw/artifacts/baseline/live_validation_smoke.txt)
   - JSON artifact: [live_validation.json](/E:/GPTProject2/Ragclaw/artifacts/baseline/live_validation.json)

5. API function smoke
   - Started local FastAPI server in-process with Uvicorn and executed `/health`, `POST /api/sessions`, `GET /api/sessions`, and `GET /api/sessions/{id}/history`.
   - Exit code: 0
   - Artifact: [api_smoke.json](/E:/GPTProject2/Ragclaw/artifacts/baseline/api_smoke.json)

6. API load smoke
   - Started local FastAPI server in-process with Uvicorn and executed 20 concurrent `/health` requests plus 10 concurrent `POST /api/sessions` requests.
   - Exit code: 0
   - Artifact: [api_load_smoke.json](/E:/GPTProject2/Ragclaw/artifacts/baseline/api_load_smoke.json)

## Baseline Results

### Full Test Baseline

Current baseline is not fully green. The most visible existing issues are:

- `test_agent_constraints` has multiple failures where event ordering/shape no longer matches expectation (`created` vs `done`, missing `tool_start`).
- `test_harness_types.test_run_metadata_serializes` expects an older `RunMetadata` shape and fails against the current metadata fields.
- `test_knowledge_multiformat_indexing` currently collides with benchmark patching and raises `KeyError` in fake knowledge astream lookup.
- `test_hitl_edit_flow` includes a fake session manager that no longer accepts the newer `message_id` persistence parameter.

This means Phase 1 should include a cleanup pass that restores full baseline compatibility before claiming parity.

### Harness Benchmark Smoke

Contract smoke passed 3/3 cases. Key values:

- route trace presence: 1.0
- retrieval trace presence: 1.0
- tool trace presence: 1.0
- capability trace presence: 1.0
- completion integrity: 1.0
- trace completeness: 1.0
- judge pass rate: 1.0

### Live Validation Smoke

Live validation passed 3/3 cases. Key values:

- retrieval trace presence: 1.0
- tool trace presence: 1.0
- completion integrity: 1.0
- session persistence integrity: 1.0
- SSE order integrity: 1.0
- trace completeness: 1.0

### API Function Smoke

- `/health`: 200 in 497.92 ms on warm startup path
- `POST /api/sessions`: 200 in 142.51 ms
- `GET /api/sessions`: 200 in 169.44 ms
- `GET /api/sessions/{id}/history`: 200 in 178.8 ms

### API Load Smoke

- `/health` x20 concurrent
  - p50: 52.8 ms
  - p95: 56.27 ms
  - max: 60.48 ms
- `POST /api/sessions` x10 concurrent
  - p50: 24.13 ms
  - p95: 38.27 ms
  - max: 38.27 ms

## Baseline Interpretation

What is stable today:

- local filesystem session persistence works
- local JSONL trace path works for benchmark/live-validation flows
- current API control plane starts and basic session CRUD is healthy
- observability smoke from the prior phase did not break the contract/live-validation benchmark harnesses

What is still fragile before infrastructure extraction:

- baseline test suite is already not fully green before the backend abstraction refactor
- there is no pluggable repository/backend layer yet
- queueing is still in-process only
- traces are still local-file only and not query-native
- there is still no `/metrics` surface

## Baseline Artifacts

- [artifacts/baseline/environment_fingerprint.json](/E:/GPTProject2/Ragclaw/artifacts/baseline/environment_fingerprint.json)
- [artifacts/baseline/unittest_discover.txt](/E:/GPTProject2/Ragclaw/artifacts/baseline/unittest_discover.txt)
- [artifacts/baseline/harness_benchmark.json](/E:/GPTProject2/Ragclaw/artifacts/baseline/harness_benchmark.json)
- [artifacts/baseline/live_validation.json](/E:/GPTProject2/Ragclaw/artifacts/baseline/live_validation.json)
- [artifacts/baseline/api_smoke.json](/E:/GPTProject2/Ragclaw/artifacts/baseline/api_smoke.json)
- [artifacts/baseline/api_load_smoke.json](/E:/GPTProject2/Ragclaw/artifacts/baseline/api_load_smoke.json)
