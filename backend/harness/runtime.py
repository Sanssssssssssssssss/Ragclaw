"""Thin harness runtime skeleton for allocating run IDs and recording lifecycle events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from harness.trace_store import RunTracePaths, RunTraceStore
from harness.types import HarnessEvent, HarnessEventName, RunMetadata, RunOutcome


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_run_id() -> str:
    return f"run-{uuid4().hex}"


@dataclass(frozen=True)
class RuntimeDependencies:
    trace_store: RunTraceStore
    now_factory: Callable[[], str] = _utc_now_iso
    run_id_factory: Callable[[], str] = _default_run_id
    event_id_factory: Callable[[], str] = _default_run_id


@dataclass(frozen=True)
class RuntimeRunHandle:
    metadata: RunMetadata
    paths: RunTracePaths

    @property
    def run_id(self) -> str:
        return self.metadata.run_id


class HarnessRuntime:
    """Own the lifecycle of a single run without owning business logic implementation."""

    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self._deps = dependencies

    def begin_run(
        self,
        *,
        user_message: str,
        session_id: str | None = None,
        source: str = "chat_api",
    ) -> RuntimeRunHandle:
        metadata = RunMetadata(
            run_id=self._deps.run_id_factory(),
            session_id=session_id,
            user_message=user_message,
            source=source,
            started_at=self._deps.now_factory(),
        )
        paths = self._deps.trace_store.create_run(metadata)
        self.record_event(
            metadata.run_id,
            "run.started",
            {
                "session_id": session_id,
                "source": source,
                "user_message": user_message,
                "started_at": metadata.started_at,
            },
        )
        return RuntimeRunHandle(metadata=metadata, paths=paths)

    def record_event(self, run_id: str, name: HarnessEventName, payload: dict[str, Any]) -> HarnessEvent:
        event = HarnessEvent(
            event_id=self._deps.event_id_factory(),
            run_id=run_id,
            name=name,
            ts=self._deps.now_factory(),
            payload=dict(payload),
        )
        self._deps.trace_store.append_event(event)
        return event

    def complete_run(
        self,
        run_id: str,
        *,
        final_answer: str = "",
        route_intent: str = "",
        used_skill: str = "",
        tool_names: tuple[str, ...] = (),
        retrieval_sources: tuple[str, ...] = (),
    ) -> RunOutcome:
        self.record_event(
            run_id,
            "run.completed",
            {
                "route_intent": route_intent,
                "used_skill": used_skill,
                "tool_names": list(tool_names),
                "retrieval_sources": list(retrieval_sources),
            },
        )
        outcome = RunOutcome(
            status="completed",
            final_answer=final_answer,
            route_intent=route_intent,
            used_skill=used_skill,
            tool_names=tool_names,
            retrieval_sources=retrieval_sources,
            completed_at=self._deps.now_factory(),
        )
        self._deps.trace_store.finalize_run(run_id, outcome)
        return outcome

    def fail_run(
        self,
        run_id: str,
        *,
        error_message: str,
        route_intent: str = "",
        used_skill: str = "",
        tool_names: tuple[str, ...] = (),
        retrieval_sources: tuple[str, ...] = (),
    ) -> RunOutcome:
        self.record_event(
            run_id,
            "run.failed",
            {
                "error_message": error_message,
                "route_intent": route_intent,
                "used_skill": used_skill,
                "tool_names": list(tool_names),
                "retrieval_sources": list(retrieval_sources),
            },
        )
        outcome = RunOutcome(
            status="failed",
            final_answer="",
            route_intent=route_intent,
            used_skill=used_skill,
            tool_names=tool_names,
            retrieval_sources=retrieval_sources,
            error_message=error_message,
            completed_at=self._deps.now_factory(),
        )
        self._deps.trace_store.finalize_run(run_id, outcome)
        return outcome

    async def run_with_executor(
        self,
        *,
        user_message: str,
        session_id: str | None = None,
        source: str = "chat_api",
        executor: Callable[[RuntimeRunHandle, "HarnessRuntime"], Awaitable[Any]],
    ) -> tuple[RuntimeRunHandle, Any]:
        handle = self.begin_run(user_message=user_message, session_id=session_id, source=source)
        result = await executor(handle, self)
        return handle, result

