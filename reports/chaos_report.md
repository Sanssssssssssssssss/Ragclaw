# Chaos-lite Report

## Scope

This report documents the actual failure-injection and resilience checks completed in the closeout pass. It explicitly separates:

- locally verified drills
- locally blocked drills
- CI-designed but not locally executed paths

## Executed Commands

1. Redis lease and expiry tests
   - Command: `backend\.venv\Scripts\python.exe -m unittest backend.tests.test_redis_queue_backend`
   - Exit code: `0`

2. External infrastructure capability detection
   - Output artifact: [artifacts/closeout/20260411T220107/machine_capabilities.json](/E:/GPTProject2/Ragclaw/artifacts/closeout/20260411T220107/machine_capabilities.json)

3. External infrastructure matrix
   - Command: `backend\.venv\Scripts\python.exe backend\benchmarks\run_external_infra_matrix.py --output artifacts\closeout\20260411T220107\external_infra_matrix.json --mode direct --allow-local-postgres-restart --postgres-dsn postgresql://postgres@127.0.0.1:35432/postgres`
   - Exit code: `0`
   - Artifact: [artifacts/closeout/20260411T220107/external_infra_matrix.json](/E:/GPTProject2/Ragclaw/artifacts/closeout/20260411T220107/external_infra_matrix.json)

4. Postgres retry drill detail
   - Artifact: [artifacts/closeout/20260411T220107/postgres_retry_drill.json](/E:/GPTProject2/Ragclaw/artifacts/closeout/20260411T220107/postgres_retry_drill.json)

## Failure Modes Covered

### Lease Expiry Detection

Covered by:

- `test_heartbeat_renews_lease_and_expiry_is_detected`
- `test_waiter_promotes_after_active_lease_expires`

Observed result:

- active lease remains valid when heartbeats succeed
- queued waiter is promoted after expiry
- stale lease checks raise `QueueLeaseLostError`

### Duplicate Release Idempotency

Covered by:

- `test_release_is_idempotent`

Observed result:

- releasing the same lease twice does not corrupt queue state

### Multi-process Same-session Non-overlap

Covered by:

- `test_two_process_workers_do_not_overlap_for_same_session`
- `redis_same_session_two_process` scenario in `infra_runtime_matrix.json`

Observed result:

- two spawned workers targeting the same session do not overlap
- effective same-session active concurrency remains `1`

### Postgres Transient Disconnect + Retry

Covered by:

- `postgres_retry_drill` in `external_infra_matrix.json`

Observed result:

- drill status: `passed`
- initial disconnect observed: `true`
- initial disconnect error: `connection timeout expired`
- restart commands:
  - stop exit code: `0`
  - start exit code: `0`
- event count after retry: `3`
- event ids remained unique: `evt-1`, `evt-2`, `evt-3`
- event sequence monotonic: `true`
- dual-write parity mismatch count: `0`

This also verified the closeout fix that suppresses duplicate JSONL writes when the same `event_id` is retried after a Postgres-side failure.

## Local Blocked Items

Still blocked in this workspace:

- real Redis restart drill
- real Redis lease-expiry drill against an external Redis daemon

Reason:

- `docker` not installed on PATH
- `redis-server` not installed on PATH

These blocked reasons are now emitted into the external matrix artifact instead of being left implicit.

## CI-designed Follow-up

Added workflow:

- `.github/workflows/infra-observability-closeout.yml`

Intended next environment:

- Docker-capable runner for the full external Redis/Postgres matrix

This workflow was added and locally dry-reviewed, but not executed from GitHub Actions during this pass.
