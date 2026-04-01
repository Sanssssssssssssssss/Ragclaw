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
from knowledge_retrieval.reranker import rerank_evidences
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
        self.assertEqual(len({item.source_path for item in diversified if "上汽集团" in item.source_path}), 1)

    def test_multi_hop_diversify_can_keep_second_family_member_for_distinct_constraints(self) -> None:
        evidences = [
            Evidence(
                source_path="knowledge/Financial Report Data/航天动力 2025 Q3.pdf",
                source_type="pdf",
                locator="page 3",
                snippet="归属于上市公司股东的净利润 -36,128,235.45 元",
                channel="fused",
                score=3.0,
                parent_id="a::3",
                chunk_type="table",
            ),
            Evidence(
                source_path="knowledge/Financial Report Data/航天动力 2025 Q3.pdf",
                source_type="pdf",
                locator="page 8",
                snippet="业绩变动原因是产品结构调整和费用增加。",
                channel="fused",
                score=2.7,
                parent_id="a::8",
                chunk_type="text",
            ),
        ]
        diversified = diversify_evidences(evidences, question_type="multi_hop", entity_hints=["航天动力"], top_k=3)
        self.assertEqual(len(diversified), 2)
        self.assertTrue(any("净利润" in item.snippet for item in diversified))
        self.assertTrue(any("原因" in item.snippet for item in diversified))

    def test_diversify_prefers_semantic_pdf_over_family_overview_for_same_family(self) -> None:
        evidences = [
            Evidence(
                source_path="knowledge/AI Knowledge/OpenAI深度报告：大模型王者，引领AGI之路.pdf",
                source_type="pdf",
                locator="family overview",
                snippet="Source PDF overview for OpenAI revenue and growth.",
                channel="fused",
                score=3.0,
                chunk_type="family_overview",
            ),
            Evidence(
                source_path="knowledge/AI Knowledge/OpenAI深度报告：大模型王者，引领AGI之路.pdf",
                source_type="pdf",
                locator="页 12 / 收入与用户",
                snippet="2025 年收入约 130 亿美元，较上年显著增长。",
                channel="fused",
                score=2.7,
                chunk_type="text-group",
            ),
        ]
        diversified = diversify_evidences(evidences, question_type="fuzzy", entity_hints=["OpenAI"], top_k=2)
        self.assertEqual(len(diversified), 1)
        self.assertEqual(diversified[0].chunk_type, "text-group")

    def test_diversify_avoids_legacy_txt_when_same_family_pdf_semantic_exists(self) -> None:
        evidences = [
            Evidence(
                source_path="knowledge/Financial Report Data/航天动力_2025_Q3.txt",
                source_type="txt",
                locator="page 1",
                snippet="净利润 -36,128,235.45",
                channel="fused",
                score=3.2,
            ),
            Evidence(
                source_path="knowledge/Financial Report Data/航天动力 2025 Q3.pdf",
                source_type="pdf",
                locator="page 3 / 利润表",
                snippet="归属于上市公司股东的净利润 -36,128,235.45",
                channel="fused",
                score=3.0,
                chunk_type="table",
            ),
        ]
        diversified = diversify_evidences(evidences, question_type="negation", entity_hints=["航天动力"], top_k=2)
        self.assertEqual(len(diversified), 1)
        self.assertTrue(diversified[0].source_path.endswith(".pdf"))

    def test_reranker_penalizes_structure_doc_when_pdf_semantic_chunk_exists(self) -> None:
        plan = build_query_plan("根据知识库，对比航天动力和上汽集团 2025 Q3 净利润正负，应检索哪些来源路径？")
        reranked = rerank_evidences(
            plan,
            [
                Evidence(
                    source_path="knowledge/Financial Report Data/data_structure.md",
                    source_type="md",
                    locator="财报目录说明",
                    snippet="财报结构说明文档",
                    channel="fused",
                    score=3.0,
                ),
                Evidence(
                    source_path="knowledge/Financial Report Data/航天动力 2025 Q3.pdf",
                    source_type="pdf",
                    locator="页 1 / 主要财务数据",
                    snippet="归属于上市公司股东的净利润 -36,128,235.45 元。",
                    channel="fused",
                    score=2.6,
                    chunk_type="table",
                ),
            ],
            top_k=2,
        )
        self.assertTrue(reranked)
        self.assertTrue(reranked[0].source_path.endswith(".pdf"))

    def test_reranker_prefers_compare_evidence_with_numeric_values(self) -> None:
        plan = build_query_plan("根据知识库，对比上汽集团与三一重工 2025 Q3 的净利润变化情况，并给出来源。")
        reranked = rerank_evidences(
            plan,
            [
                Evidence(
                    source_path="knowledge/Financial Report Data/上汽集团 2025 Q3.pdf",
                    source_type="pdf",
                    locator="page 3 / 主要会计数据",
                    snippet="归属于上市公司股东的净利润 本报告期",
                    channel="fused",
                    score=2.8,
                    chunk_type="table",
                ),
                Evidence(
                    source_path="knowledge/Financial Report Data/上汽集团 2025 Q3.pdf",
                    source_type="pdf",
                    locator="page 3 / 主要会计数据",
                    snippet="归属于上市公司股东的净利润 本报告期 644.88 同比 11.26%",
                    channel="fused",
                    score=2.6,
                    chunk_type="table",
                ),
            ],
            top_k=2,
        )
        self.assertEqual(reranked[0].snippet, "归属于上市公司股东的净利润 本报告期 644.88 同比 11.26%")

    def test_compare_targeted_retrieval_reserves_candidates_per_entity(self) -> None:
        orchestrator = KnowledgeOrchestrator()
        plan = build_query_plan("根据知识库，对比上汽集团与三一重工 2025 Q3 的净利润变化情况，并给出来源。")
        with patch(
            "knowledge_retrieval.orchestrator.hybrid_retriever.retrieve",
            side_effect=[
                HybridRetrievalResult(
                    vector_evidences=[
                        Evidence(
                            source_path="knowledge/Financial Report Data/上汽集团 2025 Q3.pdf",
                            source_type="pdf",
                            locator="page 3",
                            snippet="上汽集团 净利润 644.88 同比 11.26%",
                            channel="vector",
                            score=1.0,
                            chunk_type="table",
                        )
                    ],
                    bm25_evidences=[],
                    query_variants=["上汽集团 净利润 变化情况"],
                    entity_hints=["上汽集团"],
                ),
                HybridRetrievalResult(
                    vector_evidences=[
                        Evidence(
                            source_path="knowledge/Financial Report Data/三一重工 2025 Q3.pdf",
                            source_type="pdf",
                            locator="page 3",
                            snippet="三一重工 净利润 1,919,279 同比 48.18%",
                            channel="vector",
                            score=1.0,
                            chunk_type="table",
                        )
                    ],
                    bm25_evidences=[],
                    query_variants=["三一重工 净利润 变化情况"],
                    entity_hints=["三一重工"],
                ),
            ],
        ):
            targeted, step = orchestrator._collect_compare_targeted_evidences(plan, top_k=3)
        self.assertEqual(len(targeted), 2)
        self.assertIsNotNone(step)
        self.assertEqual(
            {item.source_path for item in targeted},
            {
                "knowledge/Financial Report Data/上汽集团 2025 Q3.pdf",
                "knowledge/Financial Report Data/三一重工 2025 Q3.pdf",
            },
        )

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

    def test_compare_final_evidence_limit_is_three(self) -> None:
        orchestrator = KnowledgeOrchestrator()
        query = "根据知识库，对比上汽集团和三一重工 2025 Q3 的财务表现，应检索哪些来源路径？"
        with patch(
            "knowledge_retrieval.orchestrator.hybrid_retriever.retrieve",
            return_value=HybridRetrievalResult(
                vector_evidences=[
                    Evidence(
                        source_path=f"knowledge/Financial Report Data/source_{index}.pdf",
                        source_type="pdf",
                        locator=f"page {index}",
                        snippet=f"evidence {index}",
                        channel="vector",
                        score=2.0 - index * 0.1,
                        parent_id=f"parent::{index}",
                    )
                    for index in range(5)
                ],
                bm25_evidences=[],
                query_variants=[query],
                entity_hints=["上汽集团", "三一重工"],
            ),
        ):
            result = orchestrator._build_formal_retrieval_result(query)
        self.assertLessEqual(len(result.evidences), 3)


if __name__ == "__main__":
    unittest.main()
