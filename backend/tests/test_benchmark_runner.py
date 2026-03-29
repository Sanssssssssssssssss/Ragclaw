from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from benchmarks.case_loader import BenchmarkSelection
from benchmarks.runner import BenchmarkRunner


class BenchmarkRunnerTraceTests(unittest.TestCase):
    def test_build_trace_prioritizes_final_knowledge_step_sources(self) -> None:
        runner = BenchmarkRunner("http://127.0.0.1:9999", selection=BenchmarkSelection(module="rag"))
        try:
            history = {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "answer",
                        "tool_calls": [],
                        "retrieval_steps": [
                            {
                                "kind": "knowledge",
                                "stage": "vector",
                                "results": [
                                    {"source_path": "knowledge/A.pdf", "snippet": "A"},
                                    {"source_path": "knowledge/B.pdf", "snippet": "B"},
                                ],
                            },
                            {
                                "kind": "knowledge",
                                "stage": "diversified",
                                "results": [
                                    {"source_path": "knowledge/C.pdf", "snippet": "C"},
                                    {"source_path": "knowledge/D.pdf", "snippet": "D"},
                                ],
                            },
                        ],
                    }
                ]
            }
            trace = runner.build_trace("session", [], history)
            self.assertEqual(trace["retrieval_sources"][:2], ["knowledge/C.pdf", "knowledge/D.pdf"])
        finally:
            runner.close()


if __name__ == "__main__":
    unittest.main()
