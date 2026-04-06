from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.backend.capabilities import build_tools_and_registry


class CapabilityRegistryTests(unittest.TestCase):
    def test_registry_includes_tools_and_skills_with_governance_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tools, registry = build_tools_and_registry(Path(temp_dir))

        capability_ids = {spec.capability_id for spec in registry.list()}
        self.assertIn("terminal", capability_ids)
        self.assertIn("python_repl", capability_ids)
        self.assertIn("read_file", capability_ids)
        self.assertIn("fetch_url", capability_ids)
        self.assertIn("mcp_filesystem_read_file", capability_ids)
        self.assertIn("mcp_filesystem_list_directory", capability_ids)
        self.assertIn("mcp_web_fetch_url", capability_ids)
        self.assertIn("skill.get_weather", capability_ids)
        self.assertIn("skill.web_search", capability_ids)

        terminal_spec = registry.get("terminal")
        self.assertEqual(terminal_spec.capability_type, "tool")
        self.assertEqual(terminal_spec.risk_level, "high")
        self.assertGreater(terminal_spec.timeout_seconds, 0)
        self.assertGreater(terminal_spec.repeated_call_limit, 0)
        self.assertIn("properties", terminal_spec.input_schema)

        skill_spec = registry.get("skill.web_search")
        self.assertEqual(skill_spec.capability_type, "skill")
        self.assertFalse(skill_spec.approval_required)
        self.assertIn("fetch_url", skill_spec.required_capabilities)

        mcp_spec = registry.get("mcp_filesystem_read_file")
        self.assertEqual(mcp_spec.capability_type, "mcp_service")
        self.assertEqual(mcp_spec.risk_level, "low")
        self.assertFalse(mcp_spec.approval_required)
        self.assertIn("filesystem", mcp_spec.tags)

        web_mcp_spec = registry.get("mcp_web_fetch_url")
        self.assertEqual(web_mcp_spec.capability_type, "mcp_service")
        self.assertEqual(web_mcp_spec.risk_level, "medium")
        self.assertFalse(web_mcp_spec.approval_required)
        self.assertIn("web", web_mcp_spec.tags)

        wrapped_names = {getattr(tool, "name", "") for tool in tools}
        self.assertEqual(
            wrapped_names,
            {
                "terminal",
                "python_repl",
                "read_file",
                "fetch_url",
                "mcp_filesystem_read_file",
                "mcp_filesystem_list_directory",
                "mcp_web_fetch_url",
            },
        )


if __name__ == "__main__":
    unittest.main()
