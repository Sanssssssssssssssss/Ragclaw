from __future__ import annotations

from src.backend.orchestration.state import GraphState


def branch_after_memory(state: GraphState) -> str:
    route_decision = state.get("route_decision")
    execution_strategy = state.get("execution_strategy")
    if route_decision is None or execution_strategy is None:
        return "direct_answer"

    if route_decision.intent == "knowledge_qa" and execution_strategy.allow_knowledge and execution_strategy.allow_retrieval:
        return "knowledge_retrieval"
    if route_decision.intent == "direct_answer" or (
        not route_decision.needs_tools and not route_decision.needs_retrieval
    ):
        return "direct_answer"
    return "capability_selection"


def branch_after_capability_selection(state: GraphState) -> str:
    if state.get("selected_capabilities"):
        return "capability_invoke"
    return "direct_answer"
