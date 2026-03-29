from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from knowledge_retrieval.evidence_organizer import diversify_evidences, merge_parent_evidences
from knowledge_retrieval.orchestrator import KnowledgeOrchestrator
from knowledge_retrieval.query_rewrite import build_query_plan
from knowledge_retrieval.types import Evidence, HybridRetrievalResult


class RetrievalEnhancementTests(unittest.TestCase):
    def test_compare_query_plan_adds_entity_targeted_rewrites(self) -> None:
        plan = build_query_plan("根据知识库，对比上汽集团和三一重工 2025 Q3 的财务表现，应检索哪些来源路径？")
        self.assertEqual(plan.question_type, "compare")
        self.assertGreaterEqual(len(plan.query_variants), 3)
        self.assertIn("上汽集团", " ".join(plan.query_variants))
        self.assertIn("三一重工", " ".join(plan.query_variants))

    def test_cross_file_and_multi_hop_queries_get_expected_question_types(self) -> None:
        cross_file = build_query_plan("根据知识库，如果要横向比较三一重工、上汽集团、航天动力三家公司的 2025 Q3 业绩，应检索哪些财报路径？")
        self.assertEqual(cross_file.question_type, "cross_file_aggregation")
        self.assertIn("上汽集团", cross_file.entity_hints)
        self.assertIn("三一重工", cross_file.entity_hints)
        self.assertIn("航天动力", cross_file.entity_hints)

        multi_hop = build_query_plan("根据知识库，哪份财报既显示净利润为负，又解释了业绩变动原因与中小投资者索赔损失有关？请给出来源路径。")
        self.assertEqual(multi_hop.question_type, "multi_hop")

    def test_parent_merge_combines_children(self) -> None:
        merged = merge_parent_evidences(
            [
                Evidence(
                    source_path="knowledge/Financial Report Data/上汽集团 2025 Q3.pdf",
                    source_type="pdf",
                    locator="page 8 / paragraph 1",
                    snippet="营业总收入 468,990,380,380.39",
                    channel="fused",
                    score=1.0,
                    parent_id="report::page8",
                ),
                Evidence(
                    source_path="knowledge/Financial Report Data/上汽集团 2025 Q3.pdf",
                    source_type="pdf",
                    locator="page 8 / paragraph 2",
                    snippet="归母净利润 69.07 亿元",
                    channel="fused",
                    score=0.9,
                    parent_id="report::page8",
                ),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].supporting_children, 2)
        self.assertIn("468,990,380,380.39", merged[0].snippet)
        self.assertIn("69.07", merged[0].snippet)

    def test_diversify_limits_same_pdf_family(self) -> None:
        evidences = [
            Evidence(
                source_path="knowledge/Financial Report Data/上汽集团 2025 Q3.pdf",
                source_type="pdf",
                locator="page 1",
                snippet="a",
                channel="fused",
                score=3.0,
            ),
            Evidence(
                source_path="knowledge/Financial Report Data/上汽集团_2025_Q3_extracted.txt",
                source_type="txt",
                locator="page 1",
                snippet="b",
                channel="fused",
                score=2.5,
            ),
            Evidence(
                source_path="knowledge/Financial Report Data/三一重工 2025 Q3.pdf",
                source_type="pdf",
                locator="page 1",
                snippet="c",
                channel="fused",
                score=2.0,
            ),
        ]
        diversified = diversify_evidences(evidences, question_type="compare", entity_hints=["上汽集团", "三一重工"], top_k=3)
        self.assertGreaterEqual(len(diversified), 2)
        self.assertIn("上汽集团", diversified[0].source_path)
        self.assertIn("三一重工", diversified[1].source_path)

    def test_compare_status_stays_partial_without_multi_source_coverage(self) -> None:
        orchestrator = KnowledgeOrchestrator()
        query = "根据知识库，对比上汽集团和三一重工 2025 Q3 的财务表现，应检索哪些来源路径？"
        with patch(
            "knowledge_retrieval.orchestrator.hybrid_retriever.retrieve",
            return_value=HybridRetrievalResult(
                vector_evidences=[
                    Evidence(
                        source_path="knowledge/Financial Report Data/上汽集团 2025 Q3.pdf",
                        source_type="pdf",
                        locator="page 1",
                        snippet="上汽集团 2025 Q3 营业收入",
                        channel="vector",
                        score=1.0,
                        parent_id="saic::page1",
                    )
                ],
                bm25_evidences=[
                    Evidence(
                        source_path="knowledge/Financial Report Data/上汽集团_2025_Q3_extracted.txt",
                        source_type="txt",
                        locator="page 1",
                        snippet="上汽集团 2025 Q3 营业收入",
                        channel="bm25",
                        score=0.8,
                        parent_id="saic::page1",
                    )
                ],
                query_variants=[query],
                entity_hints=["上汽集团", "三一重工"],
            ),
        ):
            result = orchestrator._build_formal_retrieval_result(query)
        self.assertEqual(result.status, "partial")


if __name__ == "__main__":
    unittest.main()
