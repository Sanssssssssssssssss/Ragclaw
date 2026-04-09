from __future__ import annotations


def build_route_node(orchestrator):
    async def _node(state):
        return await orchestrator.route_node(state)

    return _node


def build_skill_node(orchestrator):
    async def _node(state):
        return await orchestrator.skill_node(state)

    return _node
