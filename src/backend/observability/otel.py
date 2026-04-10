from __future__ import annotations

import logging
import os
import threading
from typing import Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Tracer

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_PROVIDER: TracerProvider | None = None
_SERVICE_NAME = "ragclaw-backend"
_SERVICE_VERSION = "0.1.0"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _should_enable_tracing(*, explicit_enable: bool | None = None, span_exporter: SpanExporter | None = None) -> bool:
    if explicit_enable is not None:
        return explicit_enable
    if span_exporter is not None:
        return True
    return any(
        (
            _truthy(os.getenv("RAGCLAW_OTEL_ENABLED")),
            _truthy(os.getenv("RAGCLAW_OTEL_CONSOLE_EXPORTER")),
            bool(str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "") or "").strip()),
        )
    )


def _build_resource(service_name: str, service_version: str) -> Resource:
    resolved_name = str(os.getenv("OTEL_SERVICE_NAME", "") or service_name).strip() or service_name
    return Resource.create(
        {
            "service.name": resolved_name,
            "service.version": service_version,
            "deployment.environment": str(os.getenv("OTEL_ENVIRONMENT", "local") or "local"),
        }
    )


def _maybe_add_otlp_exporter(provider: TracerProvider) -> None:
    endpoint = str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "") or "").strip()
    if not endpoint:
        return
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError:  # pragma: no cover - dependency guarded by requirements at runtime
        logger.warning("OTLP exporter requested but dependency is not installed")
        return
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))


def configure_otel(
    *,
    service_name: str = "ragclaw-backend",
    service_version: str = "0.1.0",
    force: bool = False,
    enable: bool | None = None,
    span_exporter: SpanExporter | None = None,
) -> bool:
    global _PROVIDER, _SERVICE_NAME, _SERVICE_VERSION

    with _LOCK:
        if force and _PROVIDER is not None:
            _PROVIDER.shutdown()
            _PROVIDER = None

        if _PROVIDER is not None:
            return True

        if not _should_enable_tracing(explicit_enable=enable, span_exporter=span_exporter):
            _SERVICE_NAME = service_name
            _SERVICE_VERSION = service_version
            return False

        provider = TracerProvider(resource=_build_resource(service_name, service_version))
        processors_added = 0
        if span_exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(span_exporter))
            processors_added += 1
        if _truthy(os.getenv("RAGCLAW_OTEL_CONSOLE_EXPORTER")):
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            processors_added += 1
        endpoint = str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "") or "").strip()
        if endpoint:
            _maybe_add_otlp_exporter(provider)
            processors_added += 1

        if processors_added == 0:
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

        _PROVIDER = provider
        _SERVICE_NAME = service_name
        _SERVICE_VERSION = service_version
        return True


def get_tracer(name: str) -> Tracer:
    with _LOCK:
        if _PROVIDER is not None:
            return _PROVIDER.get_tracer(name, _SERVICE_VERSION)
    from opentelemetry import trace

    return trace.get_tracer(name, _SERVICE_VERSION)


def otel_enabled() -> bool:
    with _LOCK:
        return _PROVIDER is not None


def shutdown_otel() -> None:
    global _PROVIDER

    with _LOCK:
        if _PROVIDER is not None:
            _PROVIDER.shutdown()
        _PROVIDER = None


__all__ = [
    "configure_otel",
    "get_tracer",
    "otel_enabled",
    "shutdown_otel",
]
