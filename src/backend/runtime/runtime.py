"""Harness runtime owning run lifecycle, trace persistence, and per-session serialization."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from uuid import uuid4

from src.backend.capabilities.governance import CapabilityBudgetPolicy, CapabilityGovernor
from src.backend.observability.trace_store import RunTracePaths, RunTraceStore
from src.backend.observability.types import HarnessEvent, HarnessEventName, RunMetadata, RunOutcome
from src.backend.runtime.policy import SessionSerialQueue


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_run_id() -> str:
    return f"run-{uuid4().hex}"


@dataclass(frozen=True)
class RuntimeDependencies:
    trace_store: RunTraceStore
    queue: SessionSerialQueue
    capability_budget_policy: CapabilityBudgetPolicy = field(default_factory=CapabilityBudgetPolicy)
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


@dataclass
class _RunState:
    route_intent: str = ""
    used_skill: str = ""
    final_answer: str = ""
    tool_names: list[str] = field(default_factory=list)
    retrieval_sources: list[str] = field(default_factory=list)
    segment_index: int = 0
    thread_id: str | None = None
    checkpoint_id: str = ""
    resume_source: str = ""
    orchestration_engine: str = ""
    run_status: str = "fresh"

    def add_tool(self, tool_name: str) -> None:
        tool = str(tool_name or "").strip()
        if tool and tool not in self.tool_names:
            self.tool_names.append(tool)

    def add_retrieval_source(self, source_path: str) -> None:
        source = str(source_path or "").strip()
        if source and source not in self.retrieval_sources:
            self.retrieval_sources.append(source)


class HarnessRuntime:
    """Own the execution lifecycle of one run while delegating business logic to executors."""

    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self._deps = dependencies
        self._run_states: dict[str, _RunState] = {}
        self._run_queues: dict[str, asyncio.Queue[HarnessEvent | None]] = {}
        self._started_events: dict[str, HarnessEvent] = {}
        self._run_governors: dict[str, CapabilityGovernor] = {}

    def now(self) -> str:
        return self._deps.now_factory()

    def begin_run(
        self,
        *,
        user_message: str,
        session_id: str | None = None,
        source: str = "chat_api",
        thread_id: str | None = None,
        checkpoint_id: str = "",
        resume_source: str = "",
        run_status: str = "fresh",
        orchestration_engine: str = "langgraph",
    ) -> RuntimeRunHandle:
        metadata = RunMetadata(
            run_id=self._deps.run_id_factory(),
            session_id=session_id,
            thread_id=thread_id,
            user_message=user_message,
            source=source,
            started_at=self._deps.now_factory(),
            orchestration_engine=orchestration_engine,
            checkpoint_id=checkpoint_id,
            resume_source=resume_source,
            run_status=run_status,
        )
        paths = self._deps.trace_store.create_run(metadata)
        self._run_states[metadata.run_id] = _RunState(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            resume_source=resume_source,
            orchestration_engine=orchestration_engine,
            run_status=run_status,
        )
        self._run_governors[metadata.run_id] = CapabilityGovernor(self._deps.capability_budget_policy)
        started_event = self.record_event(
            metadata.run_id,
            "run.started",
            {
                "session_id": session_id,
                "thread_id": thread_id,
                "source": source,
                "user_message": user_message,
                "started_at": metadata.started_at,
                "checkpoint_id": checkpoint_id,
                "resume_source": resume_source,
                "run_status": run_status,
                "orchestration_engine": orchestration_engine,
            },
        )
        self._started_events[metadata.run_id] = started_event
        return RuntimeRunHandle(metadata=metadata, paths=paths)

    def _state_for(self, handle_or_run_id: RuntimeRunHandle | str) -> _RunState:
        run_id = handle_or_run_id.run_id if isinstance(handle_or_run_id, RuntimeRunHandle) else handle_or_run_id
        state = self._run_states.get(run_id)
        if state is None:
            raise KeyError(f"unknown run state for run_id={run_id}")
        return state

    def current_segment_index(self, handle: RuntimeRunHandle) -> int:
        return self._state_for(handle).segment_index

    def advance_answer_segment(self, handle: RuntimeRunHandle) -> int:
        state = self._state_for(handle)
        state.segment_index += 1
        return state.segment_index

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

    def record_internal_event(self, run_id: str, name: HarnessEventName, payload: dict[str, Any]) -> HarnessEvent:
        event = self.record_event(run_id, name, payload)
        self._apply_event_to_state(run_id, name, payload)
        return event

    def governor_for(self, run_id: str) -> CapabilityGovernor:
        governor = self._run_governors.get(run_id)
        if governor is None:
            raise KeyError(f"unknown capability governor for run_id={run_id}")
        return governor

    async def emit(
        self,
        handle: RuntimeRunHandle,
        name: HarnessEventName,
        payload: dict[str, Any],
    ) -> HarnessEvent:
        event = self.record_event(handle.run_id, name, payload)
        self._apply_event_to_state(handle.run_id, name, payload)
        queue = self._run_queues.get(handle.run_id)
        if queue is not None:
            queue.put_nowait(event)
        return event

    def _apply_event_to_state(self, run_id: str, name: HarnessEventName, payload: dict[str, Any]) -> None:
        state = self._state_for(run_id)
        if name == "route.decided":
            state.route_intent = str(payload.get("intent", "") or "")
        elif name == "skill.decided" and payload.get("use_skill"):
            state.used_skill = str(payload.get("skill_name", "") or "")
        elif name in {"capability.completed", "capability.failed", "capability.blocked"}:
            capability_type = str(payload.get("capability_type", "") or "")
            capability_id = str(payload.get("capability_id", "") or "")
            if capability_type == "tool":
                state.add_tool(capability_id)
        elif name == "tool.started" or name == "tool.completed":
            state.add_tool(str(payload.get("tool", "") or ""))
        elif name == "retrieval.completed":
            for item in payload.get("results", []) or []:
                if isinstance(item, dict):
                    state.add_retrieval_source(str(item.get("source_path", "") or ""))
        elif name == "answer.delta":
            state.final_answer += str(payload.get("content", "") or "")
        elif name == "answer.completed":
            content = str(payload.get("content", "") or "").strip()
            if content:
                state.final_answer = content
        elif name == "checkpoint.created":
            state.checkpoint_id = str(payload.get("checkpoint_id", "") or state.checkpoint_id)
        elif name == "checkpoint.resumed":
            state.checkpoint_id = str(payload.get("checkpoint_id", "") or state.checkpoint_id)
            state.resume_source = str(payload.get("resume_source", "") or state.resume_source)
            state.run_status = "resumed"
        elif name == "checkpoint.interrupted":
            state.checkpoint_id = str(payload.get("checkpoint_id", "") or state.checkpoint_id)
            state.run_status = "interrupted"
        elif name == "hitl.requested":
            state.checkpoint_id = str(payload.get("checkpoint_id", "") or state.checkpoint_id)
            state.run_status = "interrupted"

    def complete_run(self, handle: RuntimeRunHandle) -> tuple[HarnessEvent, RunOutcome]:
        state = self._state_for(handle)
        event = self.record_event(
            handle.run_id,
            "run.completed",
            {
                "route_intent": state.route_intent,
                "used_skill": state.used_skill,
                "tool_names": list(state.tool_names),
                "retrieval_sources": list(state.retrieval_sources),
                "capability_governance": self.governor_for(handle.run_id).snapshot(),
                "thread_id": state.thread_id,
                "checkpoint_id": state.checkpoint_id,
                "resume_source": state.resume_source,
                "run_status": state.run_status,
                "orchestration_engine": state.orchestration_engine,
            },
        )
        outcome = RunOutcome(
            status="completed",
            final_answer=state.final_answer,
            route_intent=state.route_intent,
            used_skill=state.used_skill,
            tool_names=tuple(state.tool_names),
            retrieval_sources=tuple(state.retrieval_sources),
            completed_at=self._deps.now_factory(),
            thread_id=state.thread_id,
            checkpoint_id=state.checkpoint_id,
            resume_source=state.resume_source,
            run_status=state.run_status,
            orchestration_engine=state.orchestration_engine,
        )
        self._deps.trace_store.finalize_run(handle.run_id, outcome)
        self._run_states.pop(handle.run_id, None)
        self._started_events.pop(handle.run_id, None)
        self._run_governors.pop(handle.run_id, None)
        return event, outcome

    def fail_run(self, handle: RuntimeRunHandle, *, error_message: str) -> tuple[HarnessEvent, RunOutcome]:
        state = self._run_states.get(handle.run_id, _RunState())
        event = self.record_event(
            handle.run_id,
            "run.failed",
            {
                "error_message": error_message,
                "route_intent": state.route_intent,
                "used_skill": state.used_skill,
                "tool_names": list(state.tool_names),
                "retrieval_sources": list(state.retrieval_sources),
                "capability_governance": self.governor_for(handle.run_id).snapshot(),
                "thread_id": state.thread_id,
                "checkpoint_id": state.checkpoint_id,
                "resume_source": state.resume_source,
                "run_status": state.run_status,
                "orchestration_engine": state.orchestration_engine,
            },
        )
        outcome = RunOutcome(
            status="failed",
            final_answer=state.final_answer,
            route_intent=state.route_intent,
            used_skill=state.used_skill,
            tool_names=tuple(state.tool_names),
            retrieval_sources=tuple(state.retrieval_sources),
            error_message=error_message,
            completed_at=self._deps.now_factory(),
            thread_id=state.thread_id,
            checkpoint_id=state.checkpoint_id,
            resume_source=state.resume_source,
            run_status=state.run_status,
            orchestration_engine=state.orchestration_engine,
        )
        self._deps.trace_store.finalize_run(handle.run_id, outcome)
        self._run_states.pop(handle.run_id, None)
        self._started_events.pop(handle.run_id, None)
        self._run_governors.pop(handle.run_id, None)
        return event, outcome

    async def run_with_executor(
        self,
        *,
        user_message: str,
        session_id: str | None = None,
        source: str = "chat_api",
        executor,
        history: list[dict[str, Any]] | None = None,
        suppress_failures: bool = False,
        thread_id: str | None = None,
        checkpoint_id: str = "",
        resume_source: str = "",
        run_status: str = "fresh",
        orchestration_engine: str = "langgraph",
    ) -> AsyncIterator[HarnessEvent]:
        handle = self.begin_run(
            user_message=user_message,
            session_id=session_id,
            source=source,
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            resume_source=resume_source,
            run_status=run_status,
            orchestration_engine=orchestration_engine,
        )
        event_queue: asyncio.Queue[HarnessEvent | None] = asyncio.Queue()
        self._run_queues[handle.run_id] = event_queue
        started_event = self._started_events.get(handle.run_id)

        async def _drive_execution() -> Exception | None:
            lease = await self._deps.queue.acquire(session_id)
            try:
                if lease.queued:
                    await self.emit(
                        handle,
                        "run.queued",
                        {
                            "session_id": session_id,
                            "queued_at": lease.queued_at,
                        },
                    )
                    await lease.wait_until_active(self._deps.now_factory)
                    await self.emit(
                        handle,
                        "run.dequeued",
                        {
                            "session_id": session_id,
                            "queued_at": lease.queued_at,
                            "dequeued_at": lease.dequeued_at,
                            "active_started_at": lease.dequeued_at,
                        },
                    )

                await executor.execute(
                    self,
                    handle,
                    message=user_message,
                    history=list(history or []),
                )
                completion_event, _outcome = self.complete_run(handle)
                event_queue.put_nowait(completion_event)
                return None
            except Exception as exc:
                failure_event, _outcome = self.fail_run(handle, error_message=str(exc) or "unknown error")
                event_queue.put_nowait(failure_event)
                return exc
            finally:
                await self._deps.queue.release(session_id)
                self._run_queues.pop(handle.run_id, None)
                event_queue.put_nowait(None)

        driver_task = asyncio.create_task(_drive_execution())
        try:
            if started_event is not None:
                yield started_event
            while True:
                event = await event_queue.get()
                if event is None:
                    break
                yield event
            error = await driver_task
            if error is not None and not suppress_failures:
                raise error
        finally:
            self._run_queues.pop(handle.run_id, None)


def build_harness_runtime(base_dir: Path) -> HarnessRuntime:
    runs_dir = Path(base_dir) / "storage" / "runs"
    return HarnessRuntime(
        RuntimeDependencies(
            trace_store=RunTraceStore(runs_dir),
            queue=SessionSerialQueue(_utc_now_iso),
        )
    )
