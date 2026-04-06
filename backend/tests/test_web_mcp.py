from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.local_http_fixture import serve_local_http_routes
from src.backend.capabilities import build_tools_and_registry
from src.backend.capabilities.web_mcp_adapter import WebMcpFetchTool


class WebMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_mcp_fetch_success(self) -> None:
        with serve_local_http_routes(
            [{"path": "/doc", "content_type": "text/plain; charset=utf-8", "body": "hello from web mcp"}]
        ) as base_url:
            tool = WebMcpFetchTool(timeout_seconds=3)
            result = await tool.aexecute_capability({"url": f"{base_url}/doc"})
        self.assertEqual(result.status, "success")
        self.assertIn("hello from web mcp", str(result.payload.get("text", "")))

    async def test_web_mcp_invalid_url_is_failed(self) -> None:
        tool = WebMcpFetchTool(timeout_seconds=3)
        result = await tool.aexecute_capability({"url": "ftp://bad"})
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_type, "invalid_input")

    async def test_build_tools_and_registry_includes_web_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _tools, registry = build_tools_and_registry(Path(temp_dir))
        spec = registry.get("mcp_web_fetch_url")
        self.assertEqual(spec.capability_type, "mcp_service")
        self.assertEqual(spec.repeated_call_limit, 2)
