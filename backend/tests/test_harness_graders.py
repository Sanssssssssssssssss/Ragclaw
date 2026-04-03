from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from graph.agent import AgentManager
from harness.graders import KnowledgeAnswerGrader


def _evidence(source_path: str, locator: str, snippet: str):
    return SimpleNamespace(source_path=source_path, locator=locator, snippet=snippet)


def _result(*, status: str = "success", question_type: str = "compare", evidences=None, reason: str = ""):
    return SimpleNamespace(
        status=status,
        question_type=question_type,
        evidences=list(evidences or []),
        reason=reason,
    )


class HarnessGradersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = AgentManager()
        self.grader = KnowledgeAnswerGrader(self.agent)

    def test_supported_answer_passes_without_guard(self) -> None:
        result = _result(
            evidences=[
                _evidence(
                    "knowledge/report.pdf",
                    "page 1",
                    "营业收入为90亿元，同比增长12%。",
                )
            ]
        )
        decision = self.grader.grade("营业收入为90亿元，同比增长12%。", result)
        self.assertFalse(decision.downgraded)
        self.assertEqual(decision.final_answer, "营业收入为90亿元，同比增长12%。")

    def test_unsupported_number_triggers_guard(self) -> None:
        result = _result(
            evidences=[
                _evidence(
                    "knowledge/report.pdf",
                    "page 1",
                    "营业收入为90亿元，同比增长12%。",
                )
            ]
        )
        decision = self.grader.grade("营业收入为100亿元，同比增长12%。", result)
        self.assertTrue(decision.downgraded)
        self.assertEqual(decision.guard_result.details["trigger"], "unsupported_numbers_or_locators")
        self.assertIn("100亿元", decision.guard_result.details["unsupported_numbers"])

    def test_unsupported_locator_triggers_guard(self) -> None:
        result = _result(
            evidences=[
                _evidence(
                    "knowledge/report.pdf",
                    "page 1",
                    "营业收入为90亿元，同比增长12%。",
                )
            ]
        )
        decision = self.grader.grade("根据第9页，营业收入为90亿元。", result)
        self.assertTrue(decision.downgraded)
        self.assertEqual(decision.guard_result.details["trigger"], "unsupported_numbers_or_locators")
        self.assertIn("第9页", decision.guard_result.details["unsupported_locators"])

    def test_unsupported_inference_term_triggers_guard(self) -> None:
        result = _result(
            evidences=[
                _evidence(
                    "knowledge/report.pdf",
                    "page 2",
                    "净利润同比下降12%，但仍为正值。",
                )
            ]
        )
        decision = self.grader.grade("公司已经亏损。", result)
        self.assertTrue(decision.downgraded)
        self.assertEqual(decision.guard_result.details["trigger"], "unsupported_inference_terms")
        self.assertIn("亏损", decision.guard_result.details["unsupported_inference_terms"])

    def test_directory_guides_partial_answer_triggers_guard(self) -> None:
        result = _result(
            status="partial",
            question_type="multi_hop",
            evidences=[
                _evidence(
                    "knowledge/data_structure.md",
                    "section 1",
                    "This file describes the knowledge layout only.",
                )
            ],
        )
        decision = self.grader.grade("这里给出了一份完整结论。", result)
        self.assertTrue(decision.downgraded)
        self.assertEqual(decision.guard_result.details["trigger"], "directory_guides_only")


if __name__ == "__main__":
    unittest.main()
