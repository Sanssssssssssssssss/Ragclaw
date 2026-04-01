from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from knowledge_retrieval.indexer import KnowledgeIndexer
from knowledge_retrieval.opendataloader_pdf import _build_pdf_chunks
from knowledge_retrieval.orchestrator import knowledge_orchestrator
from knowledge_retrieval.types import Evidence, HybridRetrievalResult


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "opendataloader_pdf_sample.json"


class KnowledgeMultiformatIndexingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.indexer = KnowledgeIndexer()
        self.indexer.configure(BACKEND_DIR)

    def test_pdf_chunks_include_page_metadata(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        chunks, _ = _build_pdf_chunks(
            base_dir=BACKEND_DIR,
            source_relative="knowledge/Financial Report Data/sample.pdf",
            json_payload=payload,
            derived_paths={
                "derived_json_path": "storage/knowledge/derived/opendataloader/knowledge/Financial Report Data/sample/document.json",
                "derived_markdown_path": "storage/knowledge/derived/opendataloader/knowledge/Financial Report Data/sample/document.md",
            },
            has_struct_tree=True,
        )

        self.assertTrue(chunks)
        self.assertEqual(chunks[0]["source_path"], "knowledge/Financial Report Data/sample.pdf")
        self.assertEqual(chunks[0]["source_type"], "pdf")
        self.assertIn("page", chunks[0])
        self.assertIn("bbox", chunks[0])
        self.assertIn("element_type", chunks[0])
        self.assertEqual(chunks[0]["file_type"], "pdf")

    def test_excel_chunks_include_sheet_metadata(self) -> None:
        xlsx_path = BACKEND_DIR / "knowledge" / "E-commerce Data" / "sales_orders.xlsx"
        chunks = self.indexer._split_excel(xlsx_path)

        self.assertTrue(chunks)
        self.assertEqual(chunks[0]["source_path"], "knowledge/E-commerce Data/sales_orders.xlsx")
        self.assertEqual(chunks[0]["source_type"], "xlsx")
        self.assertIn("sheet", chunks[0])
        self.assertEqual(chunks[0]["sheet"], "sales_orders")
        self.assertEqual(chunks[0]["file_type"], "xlsx")
        self.assertIn("Headers:", chunks[0]["text"])
        self.assertIn("customer_id", chunks[0]["text"])

    async def test_orchestrator_uses_formal_pdf_and_excel_retrieval_without_skill(self) -> None:
        pdf_vector = Evidence(
            source_path="knowledge/Financial Report Data/sample.pdf",
            source_type="pdf",
            locator="页 1 / heading #10",
            snippet="Revenue detail from the indexed PDF chunk.",
            channel="vector",
            score=0.9,
            parent_id="knowledge/Financial Report Data/sample.pdf::element::10",
            page=1,
            bbox=[10.0, 10.0, 200.0, 40.0],
            element_type="heading",
        )
        pdf_bm25 = Evidence(
            source_path="knowledge/Financial Report Data/sample.pdf",
            source_type="pdf",
            locator="页 2 / table #20",
            snippet="Loss detail from the indexed PDF chunk.",
            channel="bm25",
            score=1.8,
            parent_id="knowledge/Financial Report Data/sample.pdf::table::20",
            page=2,
            bbox=[10.0, 200.0, 500.0, 360.0],
            element_type="table",
        )
        xlsx_vector = Evidence(
            source_path="knowledge/E-commerce Data/sales_orders.xlsx",
            source_type="xlsx",
            locator="Sheet sales_orders / rows 2-5",
            snippet="Headers: order_id, order_date, customer_id, status",
            channel="vector",
            score=0.88,
            parent_id="knowledge/E-commerce Data/sales_orders.xlsx::sheet::sales_orders::rows::2-5",
        )
        xlsx_bm25 = Evidence(
            source_path="knowledge/E-commerce Data/sales_orders.xlsx",
            source_type="xlsx",
            locator="Sheet sales_orders / overview",
            snippet="Sheet: sales_orders",
            channel="bm25",
            score=1.6,
            parent_id="knowledge/E-commerce Data/sales_orders.xlsx::sheet::sales_orders::overview",
        )

        with patch(
            "knowledge_retrieval.orchestrator.hybrid_retriever.retrieve",
            return_value=HybridRetrievalResult(
                vector_evidences=[pdf_vector, xlsx_vector],
                bm25_evidences=[pdf_bm25, xlsx_bm25],
            ),
        ):
            events = []
            async for event in knowledge_orchestrator.astream("test query"):
                events.append(event)

        result = events[-1]["result"]
        self.assertFalse(result.fallback_used)
        self.assertEqual(result.status, "success")
        self.assertTrue(any(step.stage == "vector" for step in result.steps))
        self.assertTrue(any(step.stage == "bm25" for step in result.steps))
        self.assertTrue(any(step.stage == "fused" for step in result.steps))
        self.assertTrue(any(item.source_type == "pdf" for item in result.evidences))
        self.assertTrue(any(item.source_type == "xlsx" for item in result.evidences))

    async def test_orchestrator_returns_not_found_without_skill_or_tools_when_retrieval_misses(self) -> None:
        with patch(
            "knowledge_retrieval.orchestrator.hybrid_retriever.retrieve",
            return_value=HybridRetrievalResult(vector_evidences=[], bm25_evidences=[]),
        ):
            events = []
            async for event in knowledge_orchestrator.astream("no match query"):
                events.append(event)

        result = events[-1]["result"]
        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.evidences, [])
        self.assertFalse(result.fallback_used)
        self.assertIn("does not contain enough evidence", result.reason)
        self.assertTrue(any(step.stage == "indexed_retrieval" for step in result.steps))
        self.assertFalse(any(step.stage == "skill" for step in result.steps))


if __name__ == "__main__":
    unittest.main()
