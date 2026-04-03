# Harness Migration History

## Current truth

This file is now migration history, not a forward-looking plan.

The `codex/experiments` branch has moved past shadow integration:

- live chat runs through the harness runtime
- canonical run events are the source of truth for execution traces
- per-session FIFO queueing is enforced in the runtime
- legacy SSE is produced by adapters over canonical harness events
- the knowledge-answer grader is an explicit runtime stage
- harness-native benchmark and live validation runners exist

The current architecture is described in:

- [HARNESS_ARCHITECTURE.md](/D:/GPT_Project/RAG_Model/backend/harness/HARNESS_ARCHITECTURE.md)

## What each round became in code

### Round 0

- audited the repository
- documented insertion points and rollback concerns

### Round 1

- introduced stable harness schema in [types.py](/D:/GPT_Project/RAG_Model/backend/harness/types.py)

### Round 2

- introduced append-only trace persistence in [trace_store.py](/D:/GPT_Project/RAG_Model/backend/harness/trace_store.py)

### Round 3

- introduced a dedicated runtime skeleton in [runtime.py](/D:/GPT_Project/RAG_Model/backend/harness/runtime.py)

### Round 4

- added shadow integration and canonical event adapters while preserving legacy SSE

### Round 5

- moved live chat execution ownership to the harness runtime
- reduced `AgentManager` to a capability provider and compatibility facade

### Round 6

- made the knowledge-answer grader an explicit runtime behavior through [graders.py](/D:/GPT_Project/RAG_Model/backend/harness/graders.py)

### Round 7

- added a harness-native benchmark runner in [run_harness_benchmark.py](/D:/GPT_Project/RAG_Model/backend/benchmarks/run_harness_benchmark.py)

### Round 8

- removed proven-obsolete legacy execution duplication from [agent.py](/D:/GPT_Project/RAG_Model/backend/graph/agent.py)
- retired the old shadow-era fallback logic so the harness path is the only live execution path
- aligned migration docs with the actual codebase instead of leaving stale Round 0 assumptions in place

### Round 9

- added real HTTP-level harness validation in [run_harness_live_validation.py](/D:/GPT_Project/RAG_Model/backend/benchmarks/run_harness_live_validation.py)
- validated queueing, failure handling, guarded knowledge answers, tool execution, and live SSE/session behavior through the running program

## Final migration verdict

The harness migration is complete enough that this branch no longer needs to describe itself in terms of shadow mode.

The remaining debt is cleanup debt, not ownership debt:

- `AgentManager` is still a large capability provider
- legacy SSE compatibility remains intentionally preserved for the frontend
- benchmark and live validation coverage can continue to grow

Those are normal post-migration refinements, not blockers to harness ownership.
