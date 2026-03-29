from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from benchmarks.evaluator import evaluate_case


class BenchmarkEvaluatorTests(unittest.TestCase):
    def test_pdf_and_extracted_txt_are_treated_as_same_source_family(self) -> None:
        case = {
            "id": "rag_pdf_family_case",
            "module": "rag",
            "subtype": "retrieval",
            "question_type": "direct_fact",
            "input": "test",
            "gold_sources": ["Financial Report Data/上汽集团 2025 Q3.pdf"],
            "required_source_types": ["pdf"],
            "should_have_final_answer": True,
        }
        trace = {
            "detected_route": "knowledge",
            "called_tools": [],
            "retrieval_sources": [
                "knowledge/Financial Report Data/上汽集团_2025_Q3_extracted.txt",
            ],
            "knowledge_used": True,
            "final_answer": "来源为上汽集团财报抽取文本。",
            "error_message": "",
        }

        result = evaluate_case(case, trace, {"pdf", "txt", "md", "json", "xlsx"})

        self.assertTrue(result["checks"]["retrieval_pass"])
        self.assertEqual(
            result["gold_source_families"],
            ["knowledge/Financial Report Data/上汽集团 2025 Q3.pdf"],
        )
        self.assertEqual(
            result["retrieval_source_families"],
            ["knowledge/Financial Report Data/上汽集团 2025 Q3.pdf"],
        )

    def test_report_txt_companion_maps_to_pdf_source_family(self) -> None:
        case = {
            "id": "rag_pdf_txt_family_case",
            "module": "rag",
            "subtype": "retrieval",
            "question_type": "direct_fact",
            "input": "test",
            "gold_sources": ["Financial Report Data/航天动力 2025 Q3.pdf"],
            "required_source_types": ["pdf"],
            "should_have_final_answer": True,
        }
        trace = {
            "detected_route": "knowledge",
            "called_tools": [],
            "retrieval_sources": [
                "knowledge/Financial Report Data/航天动力_2025_Q3.txt",
            ],
            "knowledge_used": True,
            "final_answer": "来源为航天动力财报文本副本。",
            "error_message": "",
        }

        result = evaluate_case(case, trace, {"pdf", "txt"})

        self.assertTrue(result["checks"]["retrieval_pass"])
        self.assertEqual(
            result["retrieval_source_families"],
            ["knowledge/Financial Report Data/航天动力 2025 Q3.pdf"],
        )

    def test_cross_file_coverage_uses_source_family_matching(self) -> None:
        case = {
            "id": "rag_cross_file_family_case",
            "module": "rag",
            "subtype": "retrieval",
            "question_type": "cross_file_aggregation",
            "input": "test",
            "gold_sources": [
                "Financial Report Data/上汽集团 2025 Q3.pdf",
                "Financial Report Data/航天动力 2025 Q3.pdf",
            ],
            "required_source_types": ["pdf"],
        }
        trace = {
            "detected_route": "knowledge",
            "called_tools": [],
            "retrieval_sources": [
                "knowledge/Financial Report Data/上汽集团_2025_Q3_extracted.txt",
                "knowledge/Financial Report Data/航天动力_2025_Q3.txt",
            ],
            "knowledge_used": True,
            "final_answer": "test",
            "error_message": "",
        }

        result = evaluate_case(case, trace, {"pdf", "txt"})

        self.assertEqual(result["source_coverage"], 1.0)
        self.assertTrue(result["checks"]["retrieval_pass"])


if __name__ == "__main__":
    unittest.main()
