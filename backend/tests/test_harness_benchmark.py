from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from benchmarks.run_harness_benchmark import run_benchmark


class HarnessBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    async def test_benchmark_runner_writes_machine_readable_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "harness_benchmark.json"
            payload = await run_benchmark(output_path)
            self.assertTrue(output_path.exists())
            stored = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["summary"]["total_cases"], 15)
            self.assertEqual(payload["summary"]["trace_completeness"], 1.0)
            self.assertEqual(payload["summary"]["guard_case_accuracy"], 1.0)
            self.assertEqual(payload["summary"]["unsupported_numeric_hallucination_rate"], 0.0)
            self.assertIn("route_skill", stored["suites"])


if __name__ == "__main__":
    unittest.main()
