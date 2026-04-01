from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from knowledge_retrieval.indexer import KnowledgeIndexer
from knowledge_retrieval.opendataloader_pdf import _build_pdf_chunks, _ensure_java_available


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "opendataloader_pdf_sample.json"


class OpenDataLoaderPdfTests(unittest.TestCase):
    def test_build_pdf_chunks_maps_real_json_field_names(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        chunks, summary = _build_pdf_chunks(
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
        self.assertEqual(summary["chunk_counts"]["text"], 1)
        self.assertEqual(summary["chunk_counts"]["table"], 1)
        self.assertEqual(summary["chunk_counts"]["figure_caption"], 1)
        self.assertGreater(summary["avg_text_chunk_length"], 40)
        self.assertGreaterEqual(summary["avg_parent_coverage_size"], 2)

        text_chunk = next(item for item in chunks if item["chunk_type"] == "text")
        self.assertEqual(text_chunk["source_path"], "knowledge/Financial Report Data/sample.pdf")
        self.assertEqual(text_chunk["page"], 1)
        self.assertEqual(text_chunk["bbox"], [10.0, 10.0, 400.0, 160.0])
        self.assertEqual(text_chunk["element_type"], "text-group")
        self.assertGreaterEqual(int(text_chunk["parent_coverage_size"]), 3)
        self.assertIn("净利润同比增长", text_chunk["text"])

        table_chunk = next(item for item in chunks if item["chunk_type"] == "table")
        self.assertEqual(table_chunk["page"], 2)
        self.assertEqual(table_chunk["table_row_count"], 2)
        self.assertEqual(table_chunk["table_column_count"], 2)
        self.assertEqual(table_chunk["table_rows"][1]["cells"][1]["text"], "100 亿元")
        self.assertIn("row 2", table_chunk["text"])
        self.assertGreaterEqual(int(table_chunk["parent_coverage_size"]), 1)

        caption_chunk = next(item for item in chunks if item["chunk_type"] == "figure-caption")
        self.assertEqual(caption_chunk["linked_content_id"], 30)
        self.assertEqual(caption_chunk["linked_content_type"], "image")
        self.assertEqual(caption_chunk["image_source"], "assets/sample-image-1.png")

    def test_indexer_pdf_parser_backend_defaults_to_opendataloader(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            indexer = KnowledgeIndexer()
            indexer.configure(BACKEND_DIR)
            self.assertEqual(indexer._pdf_parser_backend(), "opendataloader")  # noqa: SLF001

    def test_indexer_pdf_parser_backend_can_roll_back_to_legacy(self) -> None:
        with patch.dict(os.environ, {"PDF_PARSER_BACKEND": "legacy"}, clear=False):
            indexer = KnowledgeIndexer()
            indexer.configure(BACKEND_DIR)
            self.assertEqual(indexer._pdf_parser_backend(), "legacy")  # noqa: SLF001

    def test_indexer_manifest_preserves_stats(self) -> None:
        indexer = KnowledgeIndexer()
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            (base_dir / "knowledge").mkdir(parents=True, exist_ok=True)
            indexer.configure(base_dir)
            indexer._documents = [  # noqa: SLF001
                {
                    "doc_id": "x",
                    "source_path": "knowledge/sample.pdf",
                    "source_type": "pdf",
                    "locator": "椤?1 / paragraph #1",
                    "text": "sample",
                }
            ]
            indexer._build_stats = {"pdf_parser_backend": "opendataloader", "pdf_parser": {"parsed_pdf_count": 1}}  # noqa: SLF001
            indexer._build_errors = []  # noqa: SLF001
            indexer._write_manifest()  # noqa: SLF001

            restored = KnowledgeIndexer()
            restored.configure(base_dir)
            self.assertEqual(restored.build_stats()["pdf_parser_backend"], "opendataloader")

    def test_java_preflight_discovers_windows_jdk_outside_path(self) -> None:
        fake_java = Path(r"C:\Program Files\Microsoft\jdk-21\bin\java.exe")

        def fake_rglob(pattern: str):
            if pattern == "java.exe":
                return [fake_java]
            return []

        with (
            patch("knowledge_retrieval.opendataloader_pdf.shutil.which", return_value=None),
            patch.dict(os.environ, {"JAVA_HOME": "", "PATH": ""}, clear=False),
            patch("knowledge_retrieval.opendataloader_pdf.os.name", "nt"),
            patch("knowledge_retrieval.opendataloader_pdf.Path.exists", return_value=True),
            patch("knowledge_retrieval.opendataloader_pdf.Path.rglob", side_effect=fake_rglob),
        ):
            discovered = _ensure_java_available()
            self.assertIn(str(fake_java.parent), os.environ["PATH"])

        self.assertEqual(discovered, str(fake_java))


if __name__ == "__main__":
    unittest.main()
