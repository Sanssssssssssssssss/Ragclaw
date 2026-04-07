"""Harness executor bridge that delegates orchestration to the LangGraph layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.backend.knowledge import knowledge_orchestrator
from src.backend.knowledge.memory_indexer import memory_indexer
from src.backend.orchestration.executor import HarnessLangGraphOrchestrator

if TYPE_CHECKING:  # pragma: no cover
    from src.backend.runtime.agent_manager import AgentManager
    from src.backend.runtime.runtime import HarnessRuntime, RuntimeRunHandle


@dataclass
class RunSummaryState:
    route_intent: str = ""
    used_skill: str = ""
    final_answer: str = ""
    tool_names: list[str] | None = None
    retrieval_sources: list[str] | None = None

    def __post_init__(self) -> None:
        if self.tool_names is None:
            self.tool_names = []
        if self.retrieval_sources is None:
            self.retrieval_sources = []

    @classmethod
    def from_graph_state(cls, graph_state: dict[str, Any]) -> "RunSummaryState":
        route_decision = graph_state.get("route_decision")
        skill_decision = graph_state.get("skill_decision")
        capability_results = list(graph_state.get("capability_results", []))
        retrieval_sources = cls._collect_retrieval_sources(graph_state)
        tool_names = cls._collect_tool_names(graph_state, capability_results)
        return cls(
            route_intent=str(getattr(route_decision, "intent", "") or graph_state.get("path_kind", "") or ""),
            used_skill=str(getattr(skill_decision, "skill_name", "") or ""),
            final_answer=str(graph_state.get("final_answer", "") or ""),
            tool_names=tool_names,
            retrieval_sources=retrieval_sources,
        )

    @staticmethod
    def _collect_retrieval_sources(graph_state: dict[str, Any]) -> list[str]:
        sources: list[str] = []
        seen: set[str] = set()

        def _add(value: str) -> None:
            candidate = str(value or "").strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                sources.append(candidate)

        for item in graph_state.get("memory_retrieval", []) or []:
            if isinstance(item, dict):
                _add(str(item.get("source", "") or item.get("source_path", "") or ""))

        knowledge_retrieval = graph_state.get("knowledge_retrieval")
        for evidence in getattr(knowledge_retrieval, "evidences", []) or []:
            _add(str(getattr(evidence, "source_path", "") or ""))

        return sources

    @staticmethod
    def _collect_tool_names(
        graph_state: dict[str, Any],
        capability_results: list[dict[str, Any]],
    ) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()

        def _add(value: str) -> None:
            candidate = str(value or "").strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                ordered.append(candidate)

        for item in graph_state.get("recorded_tools", []) or []:
            if isinstance(item, dict):
                _add(str(item.get("tool", "") or item.get("capability_id", "") or ""))

        for item in capability_results:
            if isinstance(item, dict):
                _add(str(item.get("capability_id", "") or ""))

        return ordered


class HarnessExecutors:
    """Thin compatibility adapter around the LangGraph orchestration executor."""

    def __init__(
        self,
        agent_manager: "AgentManager",
        *,
        resume_checkpoint_id: str = "",
        resume_thread_id: str = "",
        resume_source: str = "",
        resume_payload: dict[str, Any] | None = None,
    ) -> None:
        self._agent = agent_manager
        self._resume_checkpoint_id = str(resume_checkpoint_id or "")
        self._resume_thread_id = str(resume_thread_id or "")
        self._resume_source = str(resume_source or "")
        self._resume_payload = dict(resume_payload or {})
        self._graph_executor = HarnessLangGraphOrchestrator(
            agent_manager,
            execution_support=agent_manager.create_execution_support(),
            resume_checkpoint_id=self._resume_checkpoint_id,
            resume_thread_id=self._resume_thread_id,
            resume_source=self._resume_source,
            resume_payload=self._resume_payload,
        )

    async def execute(
        self,
        runtime: "HarnessRuntime",
        handle: "RuntimeRunHandle",
        *,
        message: str,
        history: list[dict[str, Any]],
    ) -> RunSummaryState:
        graph_state = await self._graph_executor.run(
            runtime,
            handle,
            message=message,
            history=history,
        )
        return RunSummaryState.from_graph_state(graph_state)


__all__ = [
    "HarnessExecutors",
    "RunSummaryState",
    "knowledge_orchestrator",
    "memory_indexer",
]
