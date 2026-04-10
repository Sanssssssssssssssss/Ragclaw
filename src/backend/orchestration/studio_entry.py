from __future__ import annotations

from src.backend.capabilities.skills_scanner import refresh_snapshot
from src.backend.knowledge import knowledge_indexer
from src.backend.knowledge.memory_indexer import memory_indexer
from src.backend.observability.otel import configure_otel
from src.backend.orchestration.executor import HarnessLangGraphOrchestrator
from src.backend.runtime.agent_manager import agent_manager
from src.backend.runtime.config import get_settings


def build_graph():
    settings = get_settings()
    refresh_snapshot(settings.backend_dir)
    agent_manager.initialize(settings.backend_dir)
    memory_indexer.configure(settings.backend_dir)
    memory_indexer.rebuild_index()
    knowledge_indexer.configure(settings.backend_dir)
    configure_otel(service_name="ragclaw-langgraph")
    orchestrator = HarnessLangGraphOrchestrator(
        agent_manager,
        execution_support=agent_manager.create_execution_support(),
        include_checkpointer=False,
    )
    return orchestrator.graph


graph = build_graph()


__all__ = ["build_graph", "graph"]
