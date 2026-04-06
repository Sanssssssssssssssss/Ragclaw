"""Unified capability invocation contract, adapters, and runtime context."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol
from uuid import uuid4

from langchain_core.callbacks.manager import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import ConfigDict, PrivateAttr

from src.backend.capabilities.governance import CapabilityGovernor, is_retryable_error
from src.backend.capabilities.registry import CapabilityRegistry
from src.backend.capabilities.types import CapabilityInvocation, CapabilityResult, CapabilitySpec


class StructuredCapabilityTool(Protocol):
    """Protocol implemented by tools that expose structured capability results."""

    name: str
    description: str
    args_schema: Any

    def execute_capability(self, payload: dict[str, Any]) -> CapabilityResult:
        ...

    async def aexecute_capability(self, payload: dict[str, Any]) -> CapabilityResult:
        ...

    def render_capability_result(self, result: CapabilityResult) -> str:
        ...


@dataclass(frozen=True)
class CapabilityRuntimeContext:
    runtime: Any
    handle: Any
    registry: CapabilityRegistry
    governor: CapabilityGovernor

    @property
    def run_id(self) -> str:
        return str(self.handle.run_id)

    @property
    def session_id(self) -> str | None:
        return getattr(self.handle.metadata, "session_id", None)

    def now(self) -> str:
        return self.runtime.now()

    async def emit(self, name: str, payload: dict[str, Any]) -> None:
        await self.runtime.emit(self.handle, name, payload)

    def record(self, name: str, payload: dict[str, Any]) -> None:
        self.runtime.record_internal_event(self.handle.run_id, name, payload)


_CURRENT_CONTEXT: ContextVar[CapabilityRuntimeContext | None] = ContextVar("capability_runtime_context", default=None)


@asynccontextmanager
async def capability_runtime_scope(context: CapabilityRuntimeContext):
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)


def current_capability_context() -> CapabilityRuntimeContext | None:
    return _CURRENT_CONTEXT.get()


def _new_call_id() -> str:
    return f"cap-{uuid4().hex}"


def _normalize_capability_result(
    result: CapabilityResult,
    *,
    call_id: str,
    retry_count: int,
    latency_ms: int,
) -> CapabilityResult:
    return CapabilityResult(
        status=result.status,
        payload=dict(result.payload),
        partial=bool(result.partial),
        error_type=str(result.error_type or ""),
        error_message=str(result.error_message or ""),
        retryable=bool(result.retryable),
        latency_ms=latency_ms,
        call_id=call_id,
        retry_count=retry_count,
    )


def _default_text_result(value: Any, *, call_id: str, retry_count: int, latency_ms: int) -> CapabilityResult:
    text = str(value or "").strip()
    return CapabilityResult(
        status="success",
        payload={"text": text or "[no output]"},
        partial=False,
        latency_ms=latency_ms,
        call_id=call_id,
        retry_count=retry_count,
    )


def _exception_result(exc: Exception, spec: CapabilitySpec, *, call_id: str, retry_count: int, latency_ms: int) -> CapabilityResult:
    error_type = "execution_error"
    if "timed out" in str(exc).lower():
        error_type = "timeout"
    retryable = is_retryable_error(error_type, spec)
    return CapabilityResult(
        status="failed",
        payload={},
        partial=False,
        error_type=error_type,
        error_message=str(exc) or f"{spec.capability_id} failed",
        retryable=retryable,
        latency_ms=latency_ms,
        call_id=call_id,
        retry_count=retry_count,
    )


def _event_payload(
    invocation: CapabilityInvocation,
    spec: CapabilitySpec,
    *,
    status: str,
    retry_count: int,
    partial: bool,
    latency_ms: int,
    error_type: str = "",
    error_message: str = "",
    output_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "run_id": invocation.run_id,
        "session_id": invocation.session_id,
        "capability_id": spec.capability_id,
        "capability_type": spec.capability_type,
        "display_name": spec.display_name,
        "call_id": invocation.call_id,
        "status": status,
        "retry_count": retry_count,
        "partial": partial,
        "latency_ms": latency_ms,
        "error_type": error_type,
        "error_message": error_message,
        "input": dict(invocation.payload),
        "payload": dict(output_payload or {}),
        "risk_level": spec.risk_level,
        "approval_required": spec.approval_required,
        "budget_cost": spec.budget_cost,
    }


async def invoke_capability(
    *,
    spec: CapabilitySpec,
    payload: dict[str, Any],
    execute_async: Callable[[dict[str, Any]], Awaitable[CapabilityResult]],
    execute_sync: Callable[[dict[str, Any]], CapabilityResult] | None = None,
    context: CapabilityRuntimeContext | None = None,
) -> CapabilityResult:
    context = context or current_capability_context()
    call_id = _new_call_id()
    invocation = CapabilityInvocation(
        call_id=call_id,
        run_id=context.run_id if context is not None else "",
        session_id=context.session_id if context is not None else None,
        capability_id=spec.capability_id,
        capability_type=spec.capability_type,
        payload=dict(payload),
        requested_at=context.now() if context is not None else "",
        source="runtime",
    )

    if context is not None:
        decision = context.governor.check(spec)
        if not decision.allowed:
            blocked = decision.to_blocked_result(call_id=call_id)
            context.governor.record_result(spec, blocked)
            await context.emit(
                "capability.blocked",
                _event_payload(
                    invocation,
                    spec,
                    status=blocked.status,
                    retry_count=0,
                    partial=False,
                    latency_ms=0,
                    error_type=blocked.error_type,
                    error_message=blocked.error_message,
                ),
            )
            return blocked
        context.governor.record_attempt(spec)
        await context.emit(
            "capability.started",
            _event_payload(invocation, spec, status="started", retry_count=0, partial=False, latency_ms=0),
        )

    max_attempts = max(1, int(spec.retry_policy.max_retries) + 1)
    for attempt_index in range(max_attempts):
        started_at = time.perf_counter()
        try:
            result = await execute_async(dict(payload))
        except Exception as exc:  # pragma: no cover - defensive boundary
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            result = _exception_result(exc, spec, call_id=call_id, retry_count=attempt_index, latency_ms=latency_ms)
        else:
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            result = _normalize_capability_result(
                result,
                call_id=call_id,
                retry_count=attempt_index,
                latency_ms=latency_ms,
            )

        if context is not None:
            context.governor.record_result(spec, result)

        if result.status in {"success", "partial"}:
            if context is not None:
                await context.emit(
                    "capability.completed",
                    _event_payload(
                        invocation,
                        spec,
                        status=result.status,
                        retry_count=attempt_index,
                        partial=result.partial,
                        latency_ms=result.latency_ms,
                        output_payload=result.payload,
                    ),
                )
            return result

        if result.status == "blocked":
            if context is not None:
                await context.emit(
                    "capability.blocked",
                    _event_payload(
                        invocation,
                        spec,
                        status=result.status,
                        retry_count=attempt_index,
                        partial=False,
                        latency_ms=result.latency_ms,
                        error_type=result.error_type,
                        error_message=result.error_message,
                    ),
                )
            return result

        can_retry = bool(result.retryable and attempt_index + 1 < max_attempts)
        if context is not None and can_retry:
            await context.emit(
                "capability.retry",
                _event_payload(
                    invocation,
                    spec,
                    status=result.status,
                    retry_count=attempt_index,
                    partial=False,
                    latency_ms=result.latency_ms,
                    error_type=result.error_type,
                    error_message=result.error_message,
                ),
            )
        if can_retry:
            await asyncio.sleep(max(0.0, float(spec.retry_policy.backoff_seconds or 0.0)))
            continue
        if context is not None:
            await context.emit(
                "capability.failed",
                _event_payload(
                    invocation,
                    spec,
                    status=result.status,
                    retry_count=attempt_index,
                    partial=False,
                    latency_ms=result.latency_ms,
                    error_type=result.error_type,
                    error_message=result.error_message,
                ),
            )
        return result

    fallback_latency = 0
    return CapabilityResult(
        status="failed",
        payload={},
        partial=False,
        error_type="unknown_error",
        error_message=f"{spec.capability_id} failed without a final result.",
        retryable=False,
        latency_ms=fallback_latency,
        call_id=call_id,
        retry_count=max_attempts - 1,
    )


class GovernedCapabilityTool(BaseTool):
    """BaseTool adapter that routes tool execution through the capability system."""

    name: str
    description: str
    args_schema: Any
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _inner_tool: StructuredCapabilityTool = PrivateAttr()
    _capability_spec: CapabilitySpec = PrivateAttr()

    def __init__(self, inner_tool: StructuredCapabilityTool, spec: CapabilitySpec, **kwargs: Any) -> None:
        super().__init__(
            name=inner_tool.name,
            description=inner_tool.description,
            args_schema=inner_tool.args_schema,
            **kwargs,
        )
        self._inner_tool = inner_tool
        self._capability_spec = spec

    @property
    def capability_spec(self) -> CapabilitySpec:
        return self._capability_spec

    def _payload_from_call(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = {key: value for key, value in kwargs.items() if key != "run_manager"}
        if payload:
            return payload
        if not args:
            return {}
        field_names = list(getattr(self.args_schema, "model_fields", {}).keys())
        if len(args) == 1 and len(field_names) == 1:
            return {field_names[0]: args[0]}
        return {field_names[index]: value for index, value in enumerate(args) if index < len(field_names)}

    def _render_result(self, result: CapabilityResult) -> str:
        return self._inner_tool.render_capability_result(result)

    def render_capability_result(self, result: CapabilityResult) -> str:
        return self._render_result(result)

    def execute_capability(self, payload: dict[str, Any]) -> CapabilityResult:
        started_at = time.perf_counter()
        try:
            raw_result = self._inner_tool.execute_capability(payload)
        except Exception as exc:  # pragma: no cover - defensive boundary
            return _exception_result(
                exc,
                self._capability_spec,
                call_id=_new_call_id(),
                retry_count=0,
                latency_ms=int((time.perf_counter() - started_at) * 1000),
            )
        return _normalize_capability_result(
            raw_result,
            call_id=raw_result.call_id or _new_call_id(),
            retry_count=raw_result.retry_count,
            latency_ms=raw_result.latency_ms or int((time.perf_counter() - started_at) * 1000),
        )

    async def aexecute_capability(self, payload: dict[str, Any]) -> CapabilityResult:
        return await invoke_capability(
            spec=self._capability_spec,
            payload=payload,
            execute_async=self._inner_tool.aexecute_capability,
            execute_sync=self._inner_tool.execute_capability,
        )

    def _run(self, *args: Any, run_manager: CallbackManagerForToolRun | None = None, **kwargs: Any) -> str:
        payload = self._payload_from_call(*args, **kwargs)
        raw_result = self.execute_capability(payload)
        context = current_capability_context()
        if context is not None:
            invocation = CapabilityInvocation(
                call_id=raw_result.call_id or _new_call_id(),
                run_id=context.run_id,
                session_id=context.session_id,
                capability_id=self._capability_spec.capability_id,
                capability_type=self._capability_spec.capability_type,
                payload=dict(payload),
                requested_at=context.now(),
            )
            decision = context.governor.check(self._capability_spec)
            if not decision.allowed:
                blocked = decision.to_blocked_result(call_id=invocation.call_id)
                context.governor.record_result(self._capability_spec, blocked)
                context.record(
                    "capability.blocked",
                    _event_payload(
                        invocation,
                        self._capability_spec,
                        status=blocked.status,
                        retry_count=0,
                        partial=False,
                        latency_ms=0,
                        error_type=blocked.error_type,
                        error_message=blocked.error_message,
                    ),
                )
                return self._render_result(blocked)
            context.governor.record_attempt(self._capability_spec)
            context.record(
                "capability.started",
                _event_payload(
                    invocation,
                    self._capability_spec,
                    status="started",
                    retry_count=0,
                    partial=False,
                    latency_ms=0,
                ),
            )
            context.governor.record_result(self._capability_spec, raw_result)
            context.record(
                "capability.completed" if raw_result.status in {"success", "partial"} else "capability.failed",
                _event_payload(
                    invocation,
                    self._capability_spec,
                    status=raw_result.status,
                    retry_count=raw_result.retry_count,
                    partial=raw_result.partial,
                    latency_ms=raw_result.latency_ms,
                    error_type=raw_result.error_type,
                    error_message=raw_result.error_message,
                    output_payload=raw_result.payload,
                ),
            )
        return self._render_result(raw_result)

    async def _arun(self, *args: Any, run_manager: AsyncCallbackManagerForToolRun | None = None, **kwargs: Any) -> str:
        payload = self._payload_from_call(*args, **kwargs)
        result = await self.aexecute_capability(payload)
        return self._render_result(result)


def render_result_as_text(result: CapabilityResult) -> str:
    payload = dict(result.payload)
    if "text" in payload:
        return str(payload.get("text", "") or "[no output]")
    if result.error_message:
        return result.error_message
    return json.dumps(payload, ensure_ascii=False)
