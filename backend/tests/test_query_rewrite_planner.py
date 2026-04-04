from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from knowledge_retrieval.query_rewrite import QueryPlan, build_query_plan


class QueryRewritePlannerTests(unittest.TestCase):
    def test_default_build_query_plan_stays_deterministic(self) -> None:
        plan = build_query_plan("根据知识库，对比上汽集团和三一重工 2025 Q3 的净利润变化情况，并给出来源。")
        self.assertEqual(plan.planner_source, "deterministic")
        self.assertTrue(plan.query_variants)

    def test_prefer_llm_uses_llm_plan_when_available(self) -> None:
        llm_plan = QueryPlan(
            original_query="Compare OpenAI and Claude revenue.",
            question_type="compare",
            query_variants=["Compare OpenAI and Claude revenue.", "OpenAI Claude revenue compare"],
            entity_hints=["OpenAI", "Claude"],
            keyword_hints=["revenue", "compare"],
            rewrite_needed=True,
            planner_reason="llm focused the compare",
            planner_source="llm",
        )
        with patch("knowledge_retrieval.query_rewrite._LLM_REWRITE_PLANNER.plan", return_value=llm_plan):
            plan = build_query_plan("Compare OpenAI and Claude revenue.", prefer_llm=True)
        self.assertEqual(plan.planner_source, "llm")
        self.assertEqual(plan.question_type, "compare")
        self.assertEqual(plan.query_variants[1], "OpenAI Claude revenue compare")

    def test_prefer_llm_falls_back_closed_on_error(self) -> None:
        with patch("knowledge_retrieval.query_rewrite._LLM_REWRITE_PLANNER.plan", side_effect=RuntimeError("offline")):
            plan = build_query_plan("Which report mentions healthcare AI?", prefer_llm=True)
        self.assertEqual(plan.planner_source, "deterministic")
        self.assertTrue(plan.query_variants)


if __name__ == "__main__":
    unittest.main()
