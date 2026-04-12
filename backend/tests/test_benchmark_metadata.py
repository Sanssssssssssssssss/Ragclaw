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

from benchmarks.run_harness_benchmark import run_benchmark


class BenchmarkMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_harness_benchmark_output_contains_execution_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "benchmark.json"
            payload = await run_benchmark(
                output_path,
                suite="contract",
                limit=1,
                use_llm_judge=False,
                use_live_llm_decisions=False,
            )

            stored = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIn("execution_metadata", payload)
            self.assertIn("execution_metadata", stored)
            self.assertEqual(stored["summary"]["total_cases"], 1)
            self.assertIn("git_sha", stored["execution_metadata"])


if __name__ == "__main__":
    unittest.main()
