# Harness Architecture

## Status

This branch now runs the live chat path through the harness runtime.

Round 8 and Round 9 are also complete on this branch:

- Round 8: obsolete legacy execution duplication has been removed, leaving the harness path as the real production path
- Round 9: a real HTTP-level live validation runner exists to verify queueing, failure, guard, tool, and session boundaries against the running program

The current architecture is:

1. `backend/api/chat.py`
   - request boundary
   - SSE transport
   - session persistence
   - delegates run execution to the harness runtime
2. `backend/harness/runtime.py`
   - allocates `run_id`
   - owns per-run lifecycle
   - owns per-session FIFO queueing
   - records canonical events
   - finalizes traces on success and failure
3. `backend/harness/executors.py`
   - reuses existing Ragclaw capabilities
   - executes route / retrieval / tool / answer phases under runtime control
4. `backend/harness/graders.py`
   - exposes the current knowledge-answer guard as an explicit harness grader
   - produces traceable `guard.failed` decisions without changing the underlying conservative semantics
5. `backend/harness/adapters.py`
   - maps canonical harness events back to legacy SSE/session semantics
6. `backend/graph/agent.py`
   - no longer owns the live chat lifecycle
   - now primarily provides routing, skill, model, retrieval, and prompt helpers
   - `astream(...)` remains as a compatibility facade over the harness runtime

## Canonical lifecycle

The runtime emits and traces these canonical events:

- `run.started`
- `run.queued`
- `run.dequeued`
- `route.decided`
- `skill.decided`
- `retrieval.started`
- `retrieval.completed`
- `tool.started`
- `tool.completed`
- `answer.started`
- `answer.delta`
- `answer.completed`
- `guard.failed`
- `run.completed`
- `run.failed`

## Trace layout

Run traces are stored separately from session history:

- `backend/storage/runs/<run_id>.jsonl`
- `backend/storage/runs/<run_id>.summary.json`

Sessions remain user-facing conversation history under:

- `backend/sessions/*.json`

## Queueing model

Queueing is per `session_id` and FIFO.

- only one active run per session
- later requests wait instead of being rejected
- queued runs emit:
  - `run.queued`
  - `run.dequeued`

## Retrieval and planning

The harness does not replace Ragclaw retrieval.

- formal indexed retrieval remains the default knowledge path
- `backend/knowledge_retrieval/orchestrator.py` is reused as-is
- memory retrieval and knowledge retrieval are now explicit harness phases
- retrieval lifecycle is traced instead of being inferred only from frontend SSE

## Guard behavior

The existing knowledge answer guard is still implemented via `AgentManager` helper logic, but it is now surfaced explicitly through `backend/harness/graders.py` and called from the harness executor in the knowledge path.

When the guard downgrades an answer to a conservative fallback, the runtime emits:

- `guard.failed`

This keeps the current safeguard visible, traceable, and benchmarkable without introducing a heavier second guard framework.

## Compatibility

The frontend still receives legacy SSE event names:

- `retrieval`
- `tool_start`
- `tool_end`
- `new_response`
- `token`
- `done`
- `error`

This compatibility is now produced from canonical harness events, not from shadow parsing of legacy agent events.

## What is still transitional

- `backend/graph/agent.py` is no longer the runtime owner, but it is still a large dependency provider.
- `_astream_legacy_logic(...)` remains in code as transitional compatibility logic and internal reference, but it is not the production chat path.
- The knowledge grader is still a thin wrapper around existing `AgentManager` helper logic, not a fully independent policy engine.

## Benchmarking

A harness-native benchmark runner now exists at:

- `backend/benchmarks/run_harness_benchmark.py`

A live production-path validation runner also exists at:

- `backend/benchmarks/run_harness_live_validation.py`

It validates:

- lifecycle trace completeness through the real harness runtime
- lifecycle coverage through the real `HarnessExecutors` contract
- route and skill decision correctness for deterministic production-path cases
- guard-case accuracy
- unsupported numeric hallucination blocking
- unsupported locator hallucination blocking
- tool-result-to-final-answer reflection
- queue integrity

The live validation runner goes one step further and exercises the actual FastAPI program over HTTP, including:

- direct-answer path
- tool path
- guarded knowledge answer path
- failure path
- same-session queue contention

## Truthful migration verdict

This branch has a real harness production path for live chat.

It is now truthfully supportable through Round 9 because:

- live chat runs through the harness runtime as the real execution owner
- route / retrieval / tool / answer / guard / queue lifecycle all emit canonical traced events
- a harness-native benchmark exists and runs against the real runtime contract
- a live validation runner exercises the actual HTTP program boundary
- tests cover runtime lifecycle, queueing, trace persistence, chat integration, guard behavior, benchmark execution, and live validation smoke coverage

`AgentManager` is still a large capability facade rather than a minimal provider, but that is remaining cleanup debt rather than a blocker to Round 9.
