from __future__ import annotations


def build_finalize_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        return await orchestrator.finalize_node(state, config=config)

    return _node
