from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry.trace import Span, Status, StatusCode

from src.backend.observability.otel import get_tracer


def _normalize_attribute_value(value: Any) -> str | bool | int | float | list[str] | list[bool] | list[int] | list[float]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        if not values:
            return []
        if all(isinstance(item, bool) for item in values):
            return [bool(item) for item in values]
        if all(isinstance(item, int) and not isinstance(item, bool) for item in values):
            return [int(item) for item in values]
        if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in values):
            return [float(item) for item in values]
        return [str(item) for item in values]
    return str(value)


def set_span_attributes(span: Span, attributes: dict[str, Any] | None = None) -> None:
    if attributes is None:
        return
    for key, value in attributes.items():
        if value is None:
            continue
        span.set_attribute(str(key), _normalize_attribute_value(value))


def with_conversation_id(attributes: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(attributes)
    conversation_id = enriched.get("session_id") or enriched.get("thread_id")
    if conversation_id:
        enriched.setdefault("gen_ai.conversation.id", str(conversation_id))
    return enriched


@contextmanager
def with_observation(name: str, *, tracer_name: str = "ragclaw.observability", attributes: dict[str, Any] | None = None) -> Iterator[Span]:
    started_at = time.perf_counter()
    tracer = get_tracer(tracer_name)
    with tracer.start_as_current_span(name) as span:
        set_span_attributes(span, with_conversation_id(dict(attributes or {})))
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.set_attribute("error_type", type(exc).__name__)
            raise
        else:
            span.set_status(Status(StatusCode.OK))
        finally:
            span.set_attribute("latency_ms", int((time.perf_counter() - started_at) * 1000))


__all__ = [
    "set_span_attributes",
    "with_conversation_id",
    "with_observation",
]
