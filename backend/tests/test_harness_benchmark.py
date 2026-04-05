from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from benchmarks.harness_benchmark_lib import load_cases, run_selected_benchmark, summarize_results
from benchmarks.run_harness_benchmark import main, run_benchmark


class HarnessBenchmarkCaseLoadingTests(unittest.TestCase):
    def test_load_cases_supports_suite_tag_and_limit(self) -> None:
        contract_cases = load_cases(suite="contract")
        self.assertEqual(len(contract_cases), 5)
        self.assertTrue(all(case.suite == "contract" for case in contract_cases))

        tagged = load_cases(suite="contract", tag="direct")
        self.assertEqual(len(tagged), 1)
        self.assertEqual(tagged[0].case_id, "contract_direct")

        limited = load_cases(suite="all", limit=2)
        self.assertEqual(len(limited), 2)

    def test_summarize_results_preserves_useful_metrics(self) -> None:
        summary = summarize_results(
            [
                {
                    "status": "passed",
                    "route_trace_present": True,
                    "retrieval_trace_present": True,
                    "tool_trace_present": None,
                    "guard_present": False,
                    "completion_integrity": True,
                    "queue_integrity": True,
                    "trace_completeness": True,
                    "final_answer_present": True,
                    "route_correct": True,
                    "skill_correct": None,
                    "guard_correct": True,
                    "tool_result_reflected": None,
                    "counts_numeric": True,
                    "counts_locator": False,
                    "actual_guard": True,
                },
                {
                    "status": "failed",
                    "route_trace_present": True,
                    "retrieval_trace_present": False,
                    "tool_trace_present": None,
                    "guard_present": True,
                    "completion_integrity": False,
                    "queue_integrity": None,
                    "trace_completeness": False,
                    "final_answer_present": False,
                    "route_correct": False,
                    "skill_correct": None,
                    "guard_correct": False,
                    "tool_result_reflected": None,
                    "counts_numeric": False,
                    "counts_locator": True,
                    "actual_guard": False,
                },
            ]
        )
        self.assertEqual(summary["total_cases"], 2)
        self.assertEqual(summary["passed_cases"], 1)
        self.assertEqual(summary["route_trace_presence"], 1.0)
        self.assertEqual(summary["unsupported_numeric_hallucination_rate"], 0.0)
        self.assertEqual(summary["unsupported_locator_hallucination_rate"], 1.0)
        self.assertIn("capability_trace_presence", summary)


class HarnessBenchmarkRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_benchmark_writes_structured_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "harness_benchmark.json"
            payload = await run_benchmark(output_path, suite="contract", use_llm_judge=False)
            self.assertTrue(output_path.exists())
            stored = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["selection"]["suite"], "contract")
            self.assertEqual(stored["summary"]["total_cases"], 5)
            self.assertIn("contract", stored["suites"])
            self.assertEqual(payload["summary"]["trace_completeness"], 1.0)
            self.assertIn("judge", stored)
            self.assertIn("llm_available", stored["judge"])
            self.assertEqual(len(stored["cases"]), 5)

    async def test_integration_suite_smoke_case_uses_real_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "integration_smoke.json"
            payload = await run_selected_benchmark(
                suite="integration",
                tag="smoke",
                limit=1,
                output_path=output_path,
                use_llm_judge=False,
                use_live_llm_decisions=False,
            )
            self.assertEqual(payload["selection"]["suite"], "integration")
            self.assertEqual(payload["summary"]["total_cases"], 1)
            self.assertIn("integration", payload["suites"])
            self.assertEqual(payload["cases"][0]["runner"], "integration_lifecycle")
            self.assertEqual(payload["cases"][0]["status"], "passed")

    async def test_scalable_suite_loader_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "scalable_subset.json"
            payload = await run_selected_benchmark(
                suite="scalable",
                limit=3,
                output_path=output_path,
                use_llm_judge=False,
                use_live_llm_decisions=False,
            )
            self.assertEqual(payload["summary"]["total_cases"], 3)
            self.assertIn("scalable", payload["suites"])
            self.assertEqual(len(payload["cases"]), 3)

    async def test_hard_suite_exposes_judge_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "hard_suite.json"
            payload = await run_selected_benchmark(
                suite="hard",
                limit=2,
                output_path=output_path,
                use_llm_judge=False,
                use_live_llm_decisions=False,
            )
            self.assertEqual(payload["selection"]["suite"], "hard")
            self.assertIn("hard", payload["suites"])
            self.assertIn("judge_pass_rate", payload["summary"])
            self.assertIn("judge", payload)
            self.assertEqual(len(payload["cases"]), 2)
            self.assertTrue(all("judge_result" in item for item in payload["cases"]))

    async def test_rewrite_suite_outputs_planner_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "rewrite_suite.json"
            payload = await run_selected_benchmark(
                suite="rewrite",
                limit=1,
                output_path=output_path,
                use_llm_judge=False,
                use_live_llm_decisions=False,
            )
            self.assertEqual(payload["summary"]["total_cases"], 1)
            case = payload["cases"][0]
            self.assertEqual(case["runner"], "rewrite_planner")
            self.assertIn("rewritten_query", case["outcome"])
            self.assertIn("rewrite_preserves_intent", case)

    async def test_llm_judge_fields_are_exposed_with_stubbed_judge(self) -> None:
        class _StubJudge:
            available = True

            def judge_case(self, case, result, *, deterministic_judge=None):
                from src.backend.runtime.graders import HarnessLLMJudgeResult

                return HarnessLLMJudgeResult(
                    passed=True,
                    score=0.9,
                    reason="stub pass",
                    dimensions={"route_reasonable": True},
                    details={"commentary": "stub"},
                )

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "benchmarks.harness_benchmark_lib.HarnessLLMJudge.from_env",
            return_value=_StubJudge(),
        ):
            output_path = Path(temp_dir) / "llm_judge.json"
            payload = await run_selected_benchmark(
                suite="contract",
                limit=1,
                output_path=output_path,
                use_live_llm_decisions=False,
            )
            case = payload["cases"][0]
            self.assertTrue(case["llm_judge_passed"])
            self.assertEqual(case["llm_judge_score"], 0.9)
            self.assertFalse(case["judge_disagreement"])
            self.assertEqual(payload["judge"]["llm_pass_rate"], 1.0)


class HarnessBenchmarkCliTests(unittest.TestCase):
    def test_cli_basic_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "cli_harness_benchmark.json"
            rc = main(["--suite", "contract", "--limit", "1", "--deterministic-only", "--stub-decisions", "--output", str(output_path)])
            self.assertEqual(rc, 0)
            self.assertTrue(output_path.exists())
            stored = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["summary"]["total_cases"], 1)


if __name__ == "__main__":
    unittest.main()
