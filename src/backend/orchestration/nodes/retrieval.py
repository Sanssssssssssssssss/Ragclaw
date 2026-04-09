from __future__ import annotations


def build_memory_retrieval_node(orchestrator):
    async def _node(state):
        return await orchestrator.memory_retrieval_node(state)

    return _node


def build_knowledge_retrieval_node(orchestrator):
    async def _node(state):
        return await orchestrator.knowledge_retrieval_node(state)

    return _node
