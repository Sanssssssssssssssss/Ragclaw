from __future__ import annotations


def build_route_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        with orchestrator.observe_graph_node(state, node_name="route"):
            return await orchestrator.route_node(state)

    return _node


def build_skill_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        with orchestrator.observe_graph_node(state, node_name="skill"):
            return await orchestrator.skill_node(state)

    return _node
