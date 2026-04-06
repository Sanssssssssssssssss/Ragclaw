from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator


def _normalize_routes(routes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for route in routes:
        path = "/" + str(route.get("path", "") or "").lstrip("/")
        normalized[path] = dict(route)
    return normalized


@contextmanager
def serve_local_http_routes(routes: list[dict[str, Any]]) -> Iterator[str]:
    route_map = _normalize_routes(routes)

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            route = route_map.get(self.path)
            if route is None:
                body = b"not found"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            delay_seconds = float(route.get("delay_seconds", 0) or 0)
            if delay_seconds > 0:
                time.sleep(delay_seconds)

            status_code = int(route.get("status_code", 200) or 200)
            content_type = str(route.get("content_type", "text/plain; charset=utf-8") or "text/plain; charset=utf-8")
            if "body_json" in route:
                body_text = json.dumps(route.get("body_json", {}), ensure_ascii=False)
            else:
                body_text = str(route.get("body", "") or "")
            body = body_text.encode("utf-8")

            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base_url
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def substitute_web_base_url(value: Any, base_url: str) -> Any:
    if isinstance(value, str):
        return value.replace("{{web_base_url}}", base_url)
    if isinstance(value, list):
        return [substitute_web_base_url(item, base_url) for item in value]
    if isinstance(value, tuple):
        return tuple(substitute_web_base_url(item, base_url) for item in value)
    if isinstance(value, dict):
        return {key: substitute_web_base_url(item, base_url) for key, item in value.items()}
    return value
