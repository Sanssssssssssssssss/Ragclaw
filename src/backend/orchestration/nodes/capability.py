from __future__ import annotations


def build_capability_selection_node(orchestrator):
    async def _node(state):
        return await orchestrator.capability_selection_node(state)

    return _node


def build_capability_invoke_node(orchestrator):
    async def _node(state):
        return await orchestrator.capability_invoke_node(state)

    return _node


def build_capability_approval_node(orchestrator):
    async def _node(state):
        return await orchestrator.capability_approval_node(state)

    return _node


def build_capability_synthesis_node(orchestrator):
    async def _node(state):
        return await orchestrator.capability_synthesis_node(state)

    return _node


def build_capability_recovery_node(orchestrator):
    async def _node(state):
        return await orchestrator.capability_recovery_node(state)

    return _node


def build_capability_guard_node(orchestrator):
    async def _node(state):
        return await orchestrator.capability_guard_node(state)

    return _node
