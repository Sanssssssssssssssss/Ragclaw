from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.api import capabilities as capabilities_api
from src.backend.capabilities import build_tools_and_registry


class _FakeAgentManager:
    def __init__(self, root: Path) -> None:
        self.tools, self._registry = build_tools_and_registry(root)

    def get_capability_registry(self):
        return self._registry


class CapabilityApiTests(unittest.TestCase):
    def test_list_mcp_capabilities_returns_filesystem_and_web(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(capabilities_api.router, prefix="/api")
            fake_manager = _FakeAgentManager(Path(temp_dir))
            with patch.object(capabilities_api, "agent_manager", fake_manager):
                client = TestClient(app)
                response = client.get("/api/capabilities/mcp")
        self.assertEqual(response.status_code, 200)
        capability_ids = {item["capability_id"] for item in response.json()["capabilities"]}
        self.assertIn("mcp_filesystem_read_file", capability_ids)
        self.assertIn("mcp_web_fetch_url", capability_ids)


if __name__ == "__main__":
    unittest.main()
