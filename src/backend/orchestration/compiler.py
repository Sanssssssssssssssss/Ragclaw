from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.backend.orchestration.edges import branch_after_capability_selection, branch_after_memory
from src.backend.orchestration.nodes import (
    build_bootstrap_node,
    build_capability_guard_node,
    build_capability_invoke_node,
    build_capability_selection_node,
    build_capability_synthesis_node,
    build_direct_answer_node,
    build_finalize_node,
    build_knowledge_guard_node,
    build_knowledge_retrieval_node,
    build_knowledge_synthesis_node,
    build_memory_retrieval_node,
    build_route_node,
    build_skill_node,
)
from src.backend.orchestration.state import GraphState


def compile_harness_orchestration_graph(orchestrator):
    graph = StateGraph(GraphState)
    graph.add_node("bootstrap", build_bootstrap_node(orchestrator))
    graph.add_node("route", build_route_node(orchestrator))
    graph.add_node("skill", build_skill_node(orchestrator))
    graph.add_node("memory_retrieval", build_memory_retrieval_node(orchestrator))
    graph.add_node("direct_answer", build_direct_answer_node(orchestrator))
    graph.add_node("knowledge_retrieval", build_knowledge_retrieval_node(orchestrator))
    graph.add_node("knowledge_synthesis", build_knowledge_synthesis_node(orchestrator))
    graph.add_node("knowledge_guard", build_knowledge_guard_node(orchestrator))
    graph.add_node("capability_selection", build_capability_selection_node(orchestrator))
    graph.add_node("capability_invoke", build_capability_invoke_node(orchestrator))
    graph.add_node("capability_synthesis", build_capability_synthesis_node(orchestrator))
    graph.add_node("capability_guard", build_capability_guard_node(orchestrator))
    graph.add_node("finalize", build_finalize_node(orchestrator))

    graph.set_entry_point("bootstrap")
    graph.add_edge("bootstrap", "route")
    graph.add_edge("route", "skill")
    graph.add_edge("skill", "memory_retrieval")
    graph.add_conditional_edges(
        "memory_retrieval",
        branch_after_memory,
        {
            "direct_answer": "direct_answer",
            "knowledge_retrieval": "knowledge_retrieval",
            "capability_selection": "capability_selection",
        },
    )
    graph.add_edge("knowledge_retrieval", "knowledge_synthesis")
    graph.add_edge("knowledge_synthesis", "knowledge_guard")
    graph.add_edge("knowledge_guard", "finalize")
    graph.add_conditional_edges(
        "capability_selection",
        branch_after_capability_selection,
        {
            "capability_invoke": "capability_invoke",
            "direct_answer": "direct_answer",
        },
    )
    graph.add_edge("capability_invoke", "capability_synthesis")
    graph.add_edge("capability_synthesis", "capability_guard")
    graph.add_edge("capability_guard", "finalize")
    graph.add_edge("direct_answer", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
