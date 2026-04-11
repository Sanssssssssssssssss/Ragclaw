from __future__ import annotations


def build_bootstrap_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        with orchestrator.observe_graph_node(state, node_name="bootstrap"):
            return await orchestrator.bootstrap_node(state, config=config)

    return _node
