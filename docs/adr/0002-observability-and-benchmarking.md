# ADR 0002: Observability and Benchmarking

## Status
Accepted

## Context

Ragclaw already has useful lifecycle events and partial OTel span coverage, but it does not yet expose a production-grade observability surface:

- no Prometheus `/metrics`
- no standard run explorer store beyond local trace files
- no benchmark/report pipeline explicitly tied to Git SHA, environment, and infrastructure mode
- no formal dashboard/alerts package

The project needs verification and LLMOps visibility without turning observability into a parallel source of truth.

## Decision

Use canonical harness events as the source of semantic truth and derive observability from them.

Adopt:

- OpenTelemetry for traces
- Prometheus for metrics
- optional OTLP export for external sinks
- benchmark/load/chaos harnesses that write reproducible artifacts into the repository

Benchmarking will always capture:

- environment fingerprint
- Git SHA
- timestamp
- configuration mode
- raw result files
- human-readable Markdown reports

## Rationale

- keeps business truth anchored in the runtime, not in instrumentation helpers
- makes traces and metrics auditable and reproducible
- allows local-only operation while supporting enterprise sinks when configured
- gives a concrete way to compare baseline vs post-change behavior

## Consequences

Positive:

- traces, metrics, and benchmark outputs become correlated and reviewable
- failures and regressions can be tied back to concrete artifacts
- future CI integration becomes straightforward

Negative:

- extra benchmark and load scripts increase maintenance overhead
- careful instrumentation discipline is required to avoid metric/trace drift

## Guardrails

- no fabricated benchmark or observability results
- no instrumentation-only “toy” spans that do not map to real lifecycle events
- no hard dependency on external SaaS for local development
