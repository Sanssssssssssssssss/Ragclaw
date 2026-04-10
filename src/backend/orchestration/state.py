from __future__ import annotations

from typing import Any, Literal, TypedDict

from src.backend.observability.types import GuardResult
from src.backend.decision.execution_strategy import ExecutionStrategy
from src.backend.decision.lightweight_router import RoutingDecision
from src.backend.decision.skill_gate import SkillDecision


GraphPathKind = Literal["direct_answer", "knowledge_qa", "capability_path"]


class GraphState(TypedDict, total=False):
    run_id: str
    session_id: str | None
    thread_id: str
    user_message: str
    history: list[dict[str, Any]]
    augmented_history: list[dict[str, Any]]
    route_decision: RoutingDecision | None
    skill_decision: SkillDecision | None
    execution_strategy: ExecutionStrategy | None
    memory_retrieval: list[dict[str, Any]]
    knowledge_retrieval: Any | None
    selected_capabilities: list[str]
    capability_results: list[dict[str, Any]]
    answer_segments: list[str]
    final_answer: str
    guard_result: GuardResult | None
    governor_snapshot: dict[str, Any]
    interrupt_request: dict[str, Any] | None
    approval_decision: str
    working_memory: dict[str, Any]
    episodic_summary: dict[str, Any]
    recovery_attempts: dict[str, int]
    last_failure: dict[str, Any] | None
    recovery_action: str
    recovered_from_failure: bool
    recovery_metadata: dict[str, Any]
    error_state: dict[str, Any] | None
    checkpoint_meta: dict[str, Any]
    path_kind: GraphPathKind
    resolved_tools: list[Any]
    explicit_capability_payload: dict[str, Any] | None
    explicit_capability_id: str
    recorded_tools: list[dict[str, str]]
    needs_answer_synthesis: bool
    answer_usage: dict[str, int] | None
    answer_finalized: bool
    rag_mode: bool
    turn_id: str
    context_call_ids: list[str]
    selected_memory_ids: list[str]
    selected_artifact_ids: list[str]
    selected_evidence_ids: list[str]
    selected_conversation_ids: list[str]
    studio_managed_run: bool


def create_initial_graph_state(
    *,
    run_id: str,
    session_id: str | None,
    thread_id: str | None,
    user_message: str,
    history: list[dict[str, Any]],
) -> GraphState:
    resolved_thread_id = str(thread_id or session_id or run_id)
    return GraphState(
        run_id=run_id,
        session_id=session_id,
        thread_id=resolved_thread_id,
        user_message=user_message,
        history=list(history),
        augmented_history=list(history),
        route_decision=None,
        skill_decision=None,
        execution_strategy=None,
        memory_retrieval=[],
        knowledge_retrieval=None,
        selected_capabilities=[],
        capability_results=[],
        answer_segments=[],
        final_answer="",
        guard_result=None,
        governor_snapshot={},
        interrupt_request=None,
        approval_decision="",
        working_memory={},
        episodic_summary={},
        recovery_attempts={},
        last_failure=None,
        recovery_action="",
        recovered_from_failure=False,
        recovery_metadata={},
        error_state=None,
        checkpoint_meta={
            "thread_id": resolved_thread_id,
            "checkpoint_namespace": "harness_langgraph_orchestration_v1",
            "checkpoint_enabled": True,
            "graph_version": "phase1",
            "run_status": "fresh",
            "resume_source": "",
            "updated_at": "",
        },
        path_kind="direct_answer",
        resolved_tools=[],
        explicit_capability_payload=None,
        explicit_capability_id="",
        recorded_tools=[],
        needs_answer_synthesis=False,
        answer_usage=None,
        answer_finalized=False,
        rag_mode=False,
        turn_id="",
        context_call_ids=[],
        selected_memory_ids=[],
        selected_artifact_ids=[],
        selected_evidence_ids=[],
        selected_conversation_ids=[],
        studio_managed_run=False,
    )
