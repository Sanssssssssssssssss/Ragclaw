# Load Test Report

## Scope

This report covers the scaled-down load validations actually executed in this workspace after the infrastructure closeout changes. It is not a claim that the full long-duration external matrix is complete.

## Executed Commands

1. Repo-native infrastructure runtime matrix
   - Command: `backend\.venv\Scripts\python.exe backend\benchmarks\run_infra_runtime_matrix.py --mode local-only --output artifacts\closeout\20260411T220107\infra_runtime_matrix.json --load-runs 12 --load-concurrency 4 --same-session-runs 8 --same-session-concurrency 2 --soak-seconds 10 --soak-concurrency 4 --include-dualwrite --include-redis --postgres-dsn postgresql://postgres@127.0.0.1:35432/postgres`
   - Exit code: `0`
   - Artifact: [artifacts/closeout/20260411T220107/infra_runtime_matrix.json](/E:/GPTProject2/Ragclaw/artifacts/closeout/20260411T220107/infra_runtime_matrix.json)

2. Full backend unittest discovery
   - Command: `backend\.venv\Scripts\python.exe -m unittest discover backend/tests`
   - Exit code: `0`

## Results

### Infrastructure Runtime Matrix

Scaled matrix highlights:

- `local_many_sessions`
  - completed: `12/12`
  - throughput: `28.087 runs/s`
  - p50/p95 latency: `131.32 / 147.19 ms`
  - max active runs: `4`
  - serialization violations: `0`
- `local_same_session`
  - completed: `8/8`
  - throughput: `12.686 runs/s`
  - queue wait p50/p95: `69.279 / 69.605 ms`
  - max active runs: `1`
  - serialization violations: `0`
- `dualwrite_many_sessions`
  - completed: `12/12`
  - throughput: `3.787 runs/s`
  - p50/p95 latency: `981.99 / 1118.88 ms`
  - parity mismatches: `0`
- `redis_same_session_two_process`
  - worker exit codes: `0 / 0`
  - overlap violations: `0`
- `local_soak`
  - duration: `10s`
  - completed runs: `407`
  - throughput: `40.419 runs/s`
  - p50/p95/p99 latency: `95.78 / 111.69 / 153.45 ms`
  - failures: `0`

### Session Repository CRUD Roundtrip

Captured in the same artifact:

- filesystem session CRUD: `32.29 ms`
- Postgres session CRUD: `374.62 ms`

Observed behavior:

- both backends completed create -> save -> rename -> archive -> delete successfully
- no mixed-mode corruption or parity drift was observed in the local smoke path

## Notes

- The infrastructure matrix now writes `machine_capabilities.json` next to the result file.
- Local-only mode does not pretend to cover external Redis restart drills; those now surface as blocked artifacts in the external harness instead of being silently absent.
