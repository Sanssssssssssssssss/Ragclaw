from __future__ import annotations


def build_memory_retrieval_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        with orchestrator.observe_graph_node(state, node_name="memory_retrieval"):
            return await orchestrator.memory_retrieval_node(state)

    return _node


def build_knowledge_retrieval_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        with orchestrator.observe_graph_node(state, node_name="knowledge_retrieval"):
            return await orchestrator.knowledge_retrieval_node(state)

    return _node
