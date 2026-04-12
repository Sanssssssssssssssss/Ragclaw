from __future__ import annotations

import json
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

from benchmarks.infra_capabilities import detect_machine_capabilities, write_machine_capabilities


class InfraCapabilitiesTests(unittest.TestCase):
    def test_detect_machine_capabilities_reports_expected_keys(self) -> None:
        payload = detect_machine_capabilities(postgres_dsn="")
        self.assertIn("checked_at", payload)
        self.assertIn("capabilities", payload)
        self.assertIn("modes", payload)
        self.assertIn("drills", payload)
        self.assertIn("docker", payload["capabilities"])
        self.assertIn("redis_server", payload["capabilities"])
        self.assertIn("postgres_dsn", payload["capabilities"])

    def test_write_machine_capabilities_persists_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "machine_capabilities.json"
            payload = write_machine_capabilities(output_path, postgres_dsn="")
            self.assertTrue(output_path.exists())
            stored = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["checked_at"], payload["checked_at"])


if __name__ == "__main__":
    unittest.main()
