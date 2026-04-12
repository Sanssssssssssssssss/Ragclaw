from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Request, Response

from src.backend.observability.otel_spans import set_span_attributes, with_observation


class HttpTracingMiddleware:
    """Wrap FastAPI requests in a lightweight OTel span without changing app semantics."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        method = str(request.method or "").upper()
        path = str(request.url.path or "")
        route_name = str(scope.get("route").name) if scope.get("route") is not None else ""

        async def _send_wrapper(message) -> None:
            if message.get("type") == "http.response.start":
                set_span_attributes(
                    span,
                    {
                        "http.status_code": int(message.get("status", 0) or 0),
                    },
                )
            await send(message)

        with with_observation(
            "http.request",
            tracer_name="ragclaw.api",
            attributes={
                "http.method": method,
                "http.route": route_name or path or "/",
                "url.path": path or "/",
                "session_id": request.query_params.get("session_id"),
                "run_id": request.query_params.get("run_id"),
            },
        ) as span:
            set_span_attributes(
                span,
                {
                    "http.scheme": scope.get("scheme"),
                    "http.target": request.url.path,
                    "client.address": scope.get("client", ("", 0))[0] if scope.get("client") else None,
                    "client.port": scope.get("client", ("", 0))[1] if scope.get("client") else None,
                },
            )
            await self.app(scope, receive, _send_wrapper)


__all__ = ["HttpTracingMiddleware"]
