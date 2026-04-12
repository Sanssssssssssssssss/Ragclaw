# Ragclaw Observability

## What Is Instrumented

Prometheus metrics are emitted from canonical harness events, not from speculative side channels.

Current key spans:

- `http.request`
- `harness.run`
- `invoke_agent`
- `graph.node`
- `context.assemble`
- `capability.invoke`
- `tool.execute`
- `checkpoint.create`
- `checkpoint.resume`
- `hitl.request`
- `hitl.decision`
- `recovery`

Current key attributes across spans:

- `run_id`
- `session_id`
- `thread_id`
- `path_type`
- `context_path_type`
- `node_name`
- `capability_id`
- `capability_type`
- `tool_name`
- `checkpoint_id`
- `resume_source`
- `hitl_decision`
- `recovery_action`
- `orchestration_engine`
- `latency_ms`

## Prometheus Metrics

`GET /metrics` exposes:

- `ragclaw_runs_started_total`
- `ragclaw_runs_completed_total`
- `ragclaw_runs_failed_total`
- `ragclaw_run_latency_seconds`
- `ragclaw_queue_wait_seconds`
- `ragclaw_tool_calls_total{tool,status}`
- `ragclaw_retrieval_calls_total`
- `ragclaw_hitl_requests_total`
- `ragclaw_hitl_decisions_total{decision}`
- `ragclaw_checkpoint_resumes_total`
- `ragclaw_tokens_total{model,type}`
- `ragclaw_cost_usd_total`
- `ragclaw_active_runs`
- `ragclaw_pending_hitl`

## Enabling Tracing

Console exporter:

```powershell
$env:RAGCLAW_OTEL_ENABLED = "1"
$env:RAGCLAW_OTEL_CONSOLE_EXPORTER = "1"
```

OTLP exporter:

```powershell
$env:RAGCLAW_OTEL_ENABLED = "1"
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://127.0.0.1:4318/v1/traces"
```

Studio helper:

```powershell
.\backend\scripts\dev\start-langgraph-studio.ps1 -Mode dev -EnableConsoleTracing
```

## Request-to-Run Correlation

The current request path can be followed through:

1. `http.request`
2. `harness.run`
3. `invoke_agent`
4. `graph.node`
5. capability / retrieval / checkpoint / HITL spans
6. final answer and run completion metrics

This keeps the control truth in harness events while giving us a standard trace surface.

## Dashboard And Alerts

Repository artifacts:

- Grafana dashboard: [`ops/grafana/ragclaw-observability-dashboard.json`](/E:/GPTProject2/Ragclaw/ops/grafana/ragclaw-observability-dashboard.json)
- Prometheus alerts: [`ops/prometheus/ragclaw-alerts.yml`](/E:/GPTProject2/Ragclaw/ops/prometheus/ragclaw-alerts.yml)

## Validation

Focused validation:

```powershell
.\backend\scripts\dev\validate-observability.ps1
```

Key automated checks:

- compile backend
- focused runtime + checkpoint + HITL + MCP + context tests
- OTel tracing tests
- model-call context trace tests
- Studio smoke

Repository CI entry:

- `.github/workflows/infra-observability-closeout.yml`

Current closeout note:

- the workflow has been added and locally dry-reviewed
- this workspace did not execute the remote runner, so CI coverage must still be described as `designed for CI`, not `CI-verified`
