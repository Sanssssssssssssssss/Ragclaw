from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from src.backend.observability.types import HarnessEvent

_LOCK = threading.RLock()
_REGISTRY = CollectorRegistry()

RUNS_STARTED_TOTAL = Counter(
    "ragclaw_runs_started_total",
    "Total number of harness runs started.",
    registry=_REGISTRY,
)
RUNS_COMPLETED_TOTAL = Counter(
    "ragclaw_runs_completed_total",
    "Total number of harness runs completed successfully.",
    registry=_REGISTRY,
)
RUNS_FAILED_TOTAL = Counter(
    "ragclaw_runs_failed_total",
    "Total number of harness runs failed.",
    registry=_REGISTRY,
)
RUN_LATENCY_SECONDS = Histogram(
    "ragclaw_run_latency_seconds",
    "End-to-end harness run latency in seconds.",
    registry=_REGISTRY,
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
QUEUE_WAIT_SECONDS = Histogram(
    "ragclaw_queue_wait_seconds",
    "Per-session queue wait time before a run becomes active.",
    registry=_REGISTRY,
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
TOOL_CALLS_TOTAL = Counter(
    "ragclaw_tool_calls_total",
    "Count of tool calls by tool and terminal status.",
    labelnames=("tool", "status"),
    registry=_REGISTRY,
)
RETRIEVAL_CALLS_TOTAL = Counter(
    "ragclaw_retrieval_calls_total",
    "Count of retrieval completions.",
    registry=_REGISTRY,
)
HITL_REQUESTS_TOTAL = Counter(
    "ragclaw_hitl_requests_total",
    "Count of HITL requests raised by the runtime.",
    registry=_REGISTRY,
)
HITL_DECISIONS_TOTAL = Counter(
    "ragclaw_hitl_decisions_total",
    "Count of HITL decisions taken by humans.",
    labelnames=("decision",),
    registry=_REGISTRY,
)
CHECKPOINT_RESUMES_TOTAL = Counter(
    "ragclaw_checkpoint_resumes_total",
    "Count of checkpoint resume operations.",
    registry=_REGISTRY,
)
TOKENS_TOTAL = Counter(
    "ragclaw_tokens_total",
    "Observed token usage by model and token type.",
    labelnames=("model", "type"),
    registry=_REGISTRY,
)
COST_USD_TOTAL = Counter(
    "ragclaw_cost_usd_total",
    "Observed model cost in USD.",
    registry=_REGISTRY,
)
ACTIVE_RUNS = Gauge(
    "ragclaw_active_runs",
    "Number of currently active harness runs in this process.",
    registry=_REGISTRY,
)
PENDING_HITL = Gauge(
    "ragclaw_pending_hitl",
    "Number of pending HITL requests currently visible to the backend.",
    registry=_REGISTRY,
)

_RUN_STARTED_AT: dict[str, float] = {}
_PENDING_HITL_IDS: set[str] = set()


def _parse_iso(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _observe_queue_wait_seconds(payload: dict[str, Any]) -> None:
    queued_at = _parse_iso(payload.get("queued_at"))
    dequeued_at = _parse_iso(payload.get("dequeued_at"))
    if queued_at is None or dequeued_at is None:
        return
    wait_seconds = max(0.0, (dequeued_at - queued_at).total_seconds())
    QUEUE_WAIT_SECONDS.observe(wait_seconds)


def record_harness_event(event: HarnessEvent) -> None:
    payload = dict(event.payload or {})
    with _LOCK:
        if event.name == "run.started":
            RUNS_STARTED_TOTAL.inc()
            ACTIVE_RUNS.inc()
            _RUN_STARTED_AT[event.run_id] = time.perf_counter()
            return
        if event.name == "run.dequeued":
            _observe_queue_wait_seconds(payload)
            return
        if event.name == "run.completed":
            RUNS_COMPLETED_TOTAL.inc()
            ACTIVE_RUNS.dec()
            started = _RUN_STARTED_AT.pop(event.run_id, None)
            if started is not None:
                RUN_LATENCY_SECONDS.observe(max(0.0, time.perf_counter() - started))
            return
        if event.name == "run.failed":
            RUNS_FAILED_TOTAL.inc()
            ACTIVE_RUNS.dec()
            started = _RUN_STARTED_AT.pop(event.run_id, None)
            if started is not None:
                RUN_LATENCY_SECONDS.observe(max(0.0, time.perf_counter() - started))
            return
        if event.name == "tool.completed":
            TOOL_CALLS_TOTAL.labels(tool=str(payload.get("tool", "unknown") or "unknown"), status="completed").inc()
            return
        if event.name == "tool.started":
            TOOL_CALLS_TOTAL.labels(tool=str(payload.get("tool", "unknown") or "unknown"), status="started").inc()
            return
        if event.name == "capability.failed" and str(payload.get("capability_type", "") or "") == "tool":
            TOOL_CALLS_TOTAL.labels(
                tool=str(payload.get("capability_id", "unknown") or "unknown"),
                status="failed",
            ).inc()
            return
        if event.name == "retrieval.completed":
            RETRIEVAL_CALLS_TOTAL.inc()
            return
        if event.name == "hitl.requested":
            HITL_REQUESTS_TOTAL.inc()
            request_id = str(payload.get("request_id", "") or "")
            if request_id:
                _PENDING_HITL_IDS.add(request_id)
                PENDING_HITL.set(len(_PENDING_HITL_IDS))
            return
        if event.name in {"hitl.approved", "hitl.rejected", "hitl.edited"}:
            decision = event.name.split(".", maxsplit=1)[1]
            HITL_DECISIONS_TOTAL.labels(decision=decision).inc()
            request_id = str(payload.get("request_id", "") or "")
            if request_id and request_id in _PENDING_HITL_IDS:
                _PENDING_HITL_IDS.discard(request_id)
                PENDING_HITL.set(len(_PENDING_HITL_IDS))
            return
        if event.name == "checkpoint.resumed":
            CHECKPOINT_RESUMES_TOTAL.inc()
            return
        if event.name == "answer.completed":
            model = str(payload.get("model", "unknown") or "unknown")
            input_tokens = payload.get("input_tokens")
            output_tokens = payload.get("output_tokens")
            total_tokens = payload.get("total_tokens")
            if isinstance(input_tokens, (int, float)):
                TOKENS_TOTAL.labels(model=model, type="input").inc(float(input_tokens))
            if isinstance(output_tokens, (int, float)):
                TOKENS_TOTAL.labels(model=model, type="output").inc(float(output_tokens))
            if isinstance(total_tokens, (int, float)):
                TOKENS_TOTAL.labels(model=model, type="total").inc(float(total_tokens))
            cost_usd = payload.get("cost_usd")
            if isinstance(cost_usd, (int, float)):
                COST_USD_TOTAL.inc(float(cost_usd))


def set_pending_hitl(value: int) -> None:
    with _LOCK:
        PENDING_HITL.set(max(0, int(value)))


def metrics_payload() -> bytes:
    return generate_latest(_REGISTRY)


def metrics_content_type() -> str:
    return CONTENT_TYPE_LATEST


def reset_metrics_state() -> None:
    with _LOCK:
        _RUN_STARTED_AT.clear()
        _PENDING_HITL_IDS.clear()
        for metric in (
            RUNS_STARTED_TOTAL,
            RUNS_COMPLETED_TOTAL,
            RUNS_FAILED_TOTAL,
            RUN_LATENCY_SECONDS,
            QUEUE_WAIT_SECONDS,
            TOOL_CALLS_TOTAL,
            RETRIEVAL_CALLS_TOTAL,
            HITL_REQUESTS_TOTAL,
            HITL_DECISIONS_TOTAL,
            CHECKPOINT_RESUMES_TOTAL,
            TOKENS_TOTAL,
            COST_USD_TOTAL,
            ACTIVE_RUNS,
            PENDING_HITL,
        ):
            metric._metrics.clear() if hasattr(metric, "_metrics") else None  # type: ignore[attr-defined]
        ACTIVE_RUNS.set(0)
        PENDING_HITL.set(0)


__all__ = [
    "ACTIVE_RUNS",
    "PENDING_HITL",
    "metrics_content_type",
    "metrics_payload",
    "record_harness_event",
    "reset_metrics_state",
    "set_pending_hitl",
]
