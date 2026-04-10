from __future__ import annotations


def build_direct_answer_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        return await orchestrator.direct_answer_node(state)

    return _node


def build_knowledge_synthesis_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        return await orchestrator.knowledge_synthesis_node(state)

    return _node


def build_knowledge_guard_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        return await orchestrator.knowledge_guard_node(state)

    return _node
