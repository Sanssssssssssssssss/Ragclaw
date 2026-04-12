# ADR 0001: Runtime Backend Abstractions

## Status
Accepted

## Context

Ragclaw currently has clear control semantics in `HarnessRuntime`, but it depends directly on single-node implementations:

- filesystem session persistence
- JSONL run trace persistence
- in-memory per-session queueing
- local SQLite-backed checkpoint/HITL storage

This makes the runtime usable locally, but difficult to extend to multi-instance or enterprise-style deployments without entangling infrastructure concerns with canonical runtime semantics.

## Decision

Introduce explicit backend/repository abstractions that preserve existing behavior while allowing alternative implementations:

- `SessionRepository`
- `RunTraceRepository`
- `QueueBackend`
- `HitlRepository`

Keep local implementations as first-class adapters and make them the default wiring in local mode.

Runtime code should depend only on the abstractions. Concrete implementations are selected by configuration at the composition boundary.

## Rationale

- preserves `HarnessRuntime` as the lifecycle owner and canonical event source
- enables incremental migration via dual-write and fallback paths
- allows local-first default behavior to remain intact
- makes Redis/Postgres integration possible without rewriting runtime control flow
- reduces risk by allowing phase-by-phase rollout and behavior parity testing

## Consequences

Positive:

- infrastructure can evolve independently of orchestration semantics
- local and enterprise backends can coexist
- testing becomes clearer through contract tests and parity tests

Negative:

- some modules will temporarily become more verbose during the extraction
- compatibility shims are required to read historical local data

## Non-Goals

- changing canonical harness event semantics
- introducing a new runtime owner
- replacing LangGraph or the current orchestration structure
