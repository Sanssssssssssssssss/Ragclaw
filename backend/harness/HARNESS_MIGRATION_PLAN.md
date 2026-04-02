# Harness Migration Plan

## Scope

This note documents an incremental migration from the current ad hoc runtime lifecycle to a thin harness control plane.
It is grounded in the code paths currently present in this repository as of Round 0.
This round does not change production behavior.

## Current Architecture Summary

### Runtime entry points

- FastAPI startup lives in [app.py](/D:/GPT_Project/RAG_Model/backend/app.py).
- Chat execution begins at [chat.py](/D:/GPT_Project/RAG_Model/backend/api/chat.py) `POST /api/chat`.
- `chat.py` loads session state from [session_manager.py](/D:/GPT_Project/RAG_Model/backend/graph/session_manager.py), then streams events from `agent_manager.astream(...)`.
- `AgentManager` in [agent.py](/D:/GPT_Project/RAG_Model/backend/graph/agent.py) is the de facto runtime owner today.

### Execution lifecycle today

The current lifecycle inside `AgentManager.astream(...)` is roughly:

1. parse hard execution constraints via [execution_strategy.py](/D:/GPT_Project/RAG_Model/backend/graph/execution_strategy.py)
2. resolve route via deterministic rules plus [lightweight_router.py](/D:/GPT_Project/RAG_Model/backend/graph/lightweight_router.py)
3. resolve skill gate via [skill_gate.py](/D:/GPT_Project/RAG_Model/backend/graph/skill_gate.py)
4. optionally inject memory retrieval context
5. branch into:
   - direct answer path
   - knowledge retrieval path through [orchestrator.py](/D:/GPT_Project/RAG_Model/backend/knowledge_retrieval/orchestrator.py)
   - tool-agent path
6. stream ad hoc events back to the chat API
7. persist user/assistant segments in `chat.py`

### Current stream event reality

Current stream events are untyped dicts using `type` keys such as:

- `token`
- `retrieval`
- `tool_start`
- `tool_end`
- `new_response`
- `done`
- `error`
- `title`
- `skill_gate`

These are sufficient for the current frontend, but they are not a stable runtime schema:

- event names are presentation-oriented rather than lifecycle-oriented
- there is no canonical event envelope
- there is no `run_id`
- routing, skill, retrieval, tool, answer, and guard decisions are not persisted under one run trace

### Retrieval and knowledge path

- The knowledge runtime path is centralized in [orchestrator.py](/D:/GPT_Project/RAG_Model/backend/knowledge_retrieval/orchestrator.py).
- The orchestrator already has explicit stages:
  - `indexed_retrieval`
  - `query_rewrite`
  - `family_overview`
  - `vector`
  - `bm25`
  - `entity_targeted`
  - `compare_targeted`
  - `focused_targeted`
  - `fused`
  - `rerank`
  - `parent_merge`
  - `diversified`
- The orchestrator returns an `OrchestratedRetrievalResult` with:
  - `status`
  - `evidences`
  - `steps`
  - `reason`
  - `question_type`
  - `entity_hints`

This is already close to a harness-friendly phase model.

### Guarding and grading today

Knowledge guard logic is currently embedded inside [agent.py](/D:/GPT_Project/RAG_Model/backend/graph/agent.py):

- unsupported numeric detail detection
- unsupported locator detection
- unsupported high-risk inference detection
- conservative fallback answer building

This is already meaningful behavior, but it is hidden inside the runtime god-object instead of being an explicit guard/grader stage.

### Session persistence today

- Session storage is filesystem-backed in [session_manager.py](/D:/GPT_Project/RAG_Model/backend/graph/session_manager.py).
- Session records are stored under `backend/sessions/*.json`.
- Archived compressed history goes to `backend/sessions/archive/*.json`.
- The chat API persists messages after streaming segments complete.

Important current property:

- session persistence is message-oriented, not run-oriented
- there is no separate run trace store
- a single user turn may produce multiple assistant segments via `new_response`, but those segments are not grouped under a stable run object

### Benchmarks today

- Main benchmark entry is [runner.py](/D:/GPT_Project/RAG_Model/backend/benchmarks/runner.py).
- Routing-specific benchmark exists in [run_routing_benchmark.py](/D:/GPT_Project/RAG_Model/backend/benchmarks/run_routing_benchmark.py).
- Skill-gate benchmark exists in [run_skill_gate_benchmark.py](/D:/GPT_Project/RAG_Model/backend/benchmarks/run_skill_gate_benchmark.py).
- Targeted PDF benchmark exists in [run_targeted_pdf_focus.py](/D:/GPT_Project/RAG_Model/backend/benchmarks/run_targeted_pdf_focus.py).

Current benchmark behavior:

- benchmarks drive the real `/api/chat` endpoint
- they reconstruct traces indirectly from SSE events plus persisted session history
- evaluation is therefore coupled to the current chat/session contract

## Proposed Harness Insertion Points

### Insertion point 1: runtime boundary

Best first insertion point is the boundary between:

- [chat.py](/D:/GPT_Project/RAG_Model/backend/api/chat.py)
- and [agent.py](/D:/GPT_Project/RAG_Model/backend/graph/agent.py)

Safer first step:

- keep `AgentManager.astream(...)` behavior intact
- introduce a harness runtime wrapper that can:
  - allocate a `run_id`
  - emit canonical lifecycle events
  - adapt them back into the legacy SSE event stream

### Insertion point 2: trace persistence

Run traces should be stored separately from session transcripts.

Recommended location:

- `backend/storage/runs/<run_id>.jsonl`
- optionally a compact summary JSON beside it later

This keeps:

- sessions = user-visible conversation history
- runs = execution/postmortem/evaluation artifacts

### Insertion point 3: guard/grader extraction

Do not rewrite the current guard logic immediately.
Instead:

- wrap current guard decisions into an explicit grader/guard interface
- preserve semantics first
- move the code out of `AgentManager` only after trace integration is proven

### Insertion point 4: benchmark integration

Do not replace current benchmarks first.
Instead:

- keep current benchmark runners
- add a harness-native benchmark later that evaluates real run traces directly

## Compatibility Constraints

1. The current frontend expects SSE events shaped around the legacy event names.
2. `chat.py` currently segments assistant output and saves it into session history.
3. Benchmarks depend on:
   - `/api/chat`
   - `/api/sessions/.../history`
   - current retrieval/tool/done event semantics
4. Knowledge safeguards in `agent.py` must not be silently weakened during extraction.
5. The current system already has nontrivial routing, retrieval, and skill gating. The harness must wrap them, not replace them wholesale.

## Migration Order

### Round 0

- document current architecture and migration path
- no behavior change

### Round 1

- add `backend/harness/types.py`
- define canonical run/event schema
- no production path switch yet

### Round 2

- add `backend/harness/trace_store.py`
- support append-only run event persistence
- keep chat path unchanged

### Round 3

- add `backend/harness/runtime.py`
- runtime allocates `run_id` and emits `run.started`
- wrap existing execution rather than rewriting it

### Round 4

- integrate runtime in shadow/read-only mode
- real chat executions produce harness traces
- preserve legacy SSE events

### Round 5

- move lifecycle ownership gradually from `AgentManager` into harness runtime
- `AgentManager` becomes more of a facade/dependency provider

### Round 6

- extract explicit guard/grader layer

### Round 7

- add harness-native end-to-end benchmark based on run traces

### Round 8

- remove only proven-obsolete duplication
- keep compatibility surfaces where still needed

## Expected Failure Modes

1. SSE contract breakage:
   - frontend stops rendering tokens, tools, or retrieval cards correctly
2. duplicate or missing final-answer events:
   - `done` emitted twice or not at all
3. run/session drift:
   - trace says one thing, persisted session says another
4. trace corruption on exceptions:
   - abrupt failure leaves partially written artifacts
5. tool ordering mismatch:
   - `tool.completed` arrives before `tool.started`
6. retrieval loss:
   - legacy retrieval steps visible in UI but missing from harness trace, or vice versa
7. `run_id` not propagated consistently:
   - impossible to correlate API call, trace, and benchmark result
8. silent guard changes:
   - harness extraction weakens knowledge groundedness protections
9. circular imports:
   - likely if harness directly imports runtime-heavy modules without adapters
10. latency blowup:
   - too much synchronous trace I/O on the hot path

## Rollback Strategy

The migration should be reversible at each round.

Recommended rollback posture:

- keep legacy SSE event production working until the final switchover
- add harness as an adapter layer first, not a hard dependency
- keep session persistence untouched while trace store is introduced
- if runtime integration causes regression:
  - disable harness invocation at the chat boundary
  - keep `AgentManager.astream(...)` as the production path
  - retain traces only in optional/shadow mode until issues are resolved

## Round 0 Conclusion

This repository does not need a wholesale runtime rewrite.
It already has enough structured behavior to support a thin harness control plane.

The safe path is:

1. define stable harness types
2. add append-only trace persistence
3. wrap the existing runtime in shadow mode
4. only then move lifecycle ownership outward from `AgentManager`

Anything more aggressive would create unnecessary regression risk against:

- the current SSE frontend
- session persistence
- knowledge safeguards
- benchmark compatibility
