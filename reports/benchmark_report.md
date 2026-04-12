# Benchmark Report

## Scope

This report summarizes the closeout validation artifacts produced after adding:

- infrastructure capability detection
- Postgres session repository
- session repository parity checks
- external-infra drill harness with blocked/runnable outputs
- retry-safe dual-write event handling

## Executed Commands

1. Harness benchmark smoke
   - Command: `backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_benchmark.py --suite contract --deterministic-only --stub-decisions --limit 3 --output artifacts\closeout\20260411T220107\harness_benchmark.json`
   - Exit code: `0`

2. Live validation smoke
   - Command: `backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_live_validation.py --limit 3 --output artifacts\closeout\20260411T220107\live_validation.json`
   - Exit code: `0`

3. Session repository parity
   - Command: `backend\.venv\Scripts\python.exe backend\benchmarks\run_session_repository_parity.py --postgres-dsn postgresql://postgres@127.0.0.1:35432/postgres --output artifacts\closeout\20260411T220107\session_repository_parity.json`
   - Exit code: `0`

## Results

### Harness Benchmark Smoke

Artifact:

- [artifacts/closeout/20260411T220107/harness_benchmark.json](/E:/GPTProject2/Ragclaw/artifacts/closeout/20260411T220107/harness_benchmark.json)

Summary:

- total cases: `3`
- passed: `3`
- failed: `0`
- route trace presence: `1.0`
- retrieval trace presence: `1.0`
- tool trace presence: `1.0`
- capability trace presence: `1.0`
- capability governance visibility: `1.0`
- completion integrity: `1.0`
- trace completeness: `1.0`

### Live Validation Smoke

Artifact:

- [artifacts/closeout/20260411T220107/live_validation.json](/E:/GPTProject2/Ragclaw/artifacts/closeout/20260411T220107/live_validation.json)

Summary:

- total cases: `3`
- passed: `3`
- failed: `0`
- retrieval trace presence: `1.0`
- tool trace presence: `1.0`
- completion integrity: `1.0`
- session persistence integrity: `1.0`
- SSE order integrity: `1.0`
- trace completeness: `1.0`

### Session Repository Parity

Artifact:

- [artifacts/closeout/20260411T220107/session_repository_parity.json](/E:/GPTProject2/Ragclaw/artifacts/closeout/20260411T220107/session_repository_parity.json)

Summary:

- status: `passed`
- mismatches: `[]`
- imported sessions: `1`
- imported messages: `2`

## Execution Metadata

All benchmark JSON payloads now include `execution_metadata` with:

- capture time
- Git SHA
- Python version
- platform
- working directory
- selected backend and observability environment variables
- benchmark selection config

Infrastructure-oriented outputs additionally include:

- `machine_capabilities.json`
- explicit blocked drill reasons
- local-only vs external-infra mode selection

## Notes

- This report is based on local execution only.
- CI workflow support was added in `.github/workflows/infra-observability-closeout.yml`, but that workflow has not been executed from GitHub in this closeout pass.
