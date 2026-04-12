from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks import run_external_infra_matrix


class ExternalInfraMatrixTests(unittest.TestCase):
    def test_require_external_infra_writes_blocked_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "external_infra_matrix.json"
            blocked_capabilities = {
                "modes": {"external_infra": {"available": False}},
                "capabilities": {"docker": {"available": False}, "redis_server": {"available": False}, "postgres_dsn": {"available": False}, "postgres_local_scripts": {"available": False}},
                "drills": {},
            }
            with patch.object(run_external_infra_matrix, "write_machine_capabilities", return_value=blocked_capabilities):
                exit_code = run_external_infra_matrix.main(
                    [
                        "--output",
                        str(output_path),
                        "--require-external-infra",
                    ]
                )
            self.assertEqual(exit_code, 2)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["blocked"]), 1)

    def test_blocked_output_is_still_emitted_without_require_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "external_infra_matrix.json"
            blocked_capabilities = {
                "modes": {"external_infra": {"available": False}},
                "capabilities": {"docker": {"available": False}, "redis_server": {"available": False}, "postgres_dsn": {"available": False}, "postgres_local_scripts": {"available": False}},
                "drills": {
                    "redis_restart_drill": {"blocked_reason": "redis missing"},
                    "postgres_transient_disconnect_drill": {"blocked_reason": "postgres missing"},
                },
            }
            with patch.object(run_external_infra_matrix, "write_machine_capabilities", return_value=blocked_capabilities):
                exit_code = run_external_infra_matrix.main(
                    [
                        "--output",
                        str(output_path),
                    ]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["drills"], [])
            self.assertGreaterEqual(len(payload["blocked"]), 1)


if __name__ == "__main__":
    unittest.main()
