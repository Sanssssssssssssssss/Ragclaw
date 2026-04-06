from __future__ import annotations


def build_bootstrap_node(orchestrator):
    async def _node(state):
        return await orchestrator.bootstrap_node(state)

    return _node
