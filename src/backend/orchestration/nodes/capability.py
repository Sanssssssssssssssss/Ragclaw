from __future__ import annotations


def build_capability_selection_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        with orchestrator.observe_graph_node(state, node_name="capability_selection"):
            return await orchestrator.capability_selection_node(state)

    return _node


def build_capability_invoke_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        with orchestrator.observe_graph_node(state, node_name="capability_invoke"):
            return await orchestrator.capability_invoke_node(state)

    return _node


def build_capability_approval_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        with orchestrator.observe_graph_node(state, node_name="capability_approval"):
            return await orchestrator.capability_approval_node(state)

    return _node


def build_capability_synthesis_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        with orchestrator.observe_graph_node(state, node_name="capability_synthesis"):
            return await orchestrator.capability_synthesis_node(state)

    return _node


def build_capability_recovery_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        with orchestrator.observe_graph_node(state, node_name="capability_recovery"):
            return await orchestrator.capability_recovery_node(state)

    return _node


def build_capability_guard_node(orchestrator):
    async def _node(state, config=None):
        orchestrator.ensure_graph_bindings(state, config=config)
        with orchestrator.observe_graph_node(state, node_name="capability_guard"):
            return await orchestrator.capability_guard_node(state)

    return _node
