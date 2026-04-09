from __future__ import annotations


def build_finalize_node(orchestrator):
    async def _node(state):
        return await orchestrator.finalize_node(state)

    return _node
