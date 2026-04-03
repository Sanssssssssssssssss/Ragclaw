from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from benchmarks.organize_benchmark_storage import organize_benchmark_storage
from benchmarks.storage_layout import (
    BENCHMARK_ROOT,
    classify_benchmark_entry,
    harness_output_path,
    rag_general_output_dir,
    rag_pdf_output_path,
    routing_output_path,
    skill_gate_output_path,
)


class BenchmarkStorageLayoutTests(unittest.TestCase):
    def test_default_paths_are_categorized(self) -> None:
        self.assertEqual(harness_output_path(), BENCHMARK_ROOT / "harness" / "harness_benchmark_latest.json")
        self.assertEqual(routing_output_path(), BENCHMARK_ROOT / "routing" / "routing_benchmark_latest.json")
        self.assertEqual(skill_gate_output_path(), BENCHMARK_ROOT / "skill_gate" / "skill_gate_benchmark_latest.json")
        self.assertEqual(rag_pdf_output_path(), BENCHMARK_ROOT / "rag" / "pdf_targeted" / "pdf_targeted_after_focus.json")
        self.assertEqual(rag_general_output_dir(), BENCHMARK_ROOT / "rag" / "general")

    def test_classify_benchmark_entry_maps_known_patterns(self) -> None:
        self.assertEqual(
            classify_benchmark_entry(Path("harness_benchmark_latest.json")),
            BENCHMARK_ROOT / "harness" / "harness_benchmark_latest.json",
        )
        self.assertEqual(
            classify_benchmark_entry(Path("routing_benchmark_latest.json")),
            BENCHMARK_ROOT / "routing" / "routing_benchmark_latest.json",
        )
        self.assertEqual(
            classify_benchmark_entry(Path("pdf_targeted_after_focus.json")),
            BENCHMARK_ROOT / "rag" / "pdf_targeted" / "pdf_targeted_after_focus.json",
        )
        self.assertEqual(
            classify_benchmark_entry(Path("benchmark-results-20260327-232520.json")),
            BENCHMARK_ROOT / "rag" / "general" / "benchmark-results-20260327-232520.json",
        )
        self.assertEqual(
            classify_benchmark_entry(Path("backend-8035.log")),
            BENCHMARK_ROOT / "logs" / "backend-8035.log",
        )

    def test_organize_benchmark_storage_moves_files_into_subdirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "harness_benchmark_latest.json").write_text("{}", encoding="utf-8")
            (root / "routing_benchmark_latest.json").write_text("{}", encoding="utf-8")
            (root / "pdf_targeted_after_focus.json").write_text("{}", encoding="utf-8")
            (root / "benchmark-results-20260327-232520.json").write_text("{}", encoding="utf-8")
            (root / "backend-8035.log").write_text("{}", encoding="utf-8")

            planned = organize_benchmark_storage(root)

            self.assertEqual(len(planned), 5)
            self.assertTrue((root / "harness" / "harness_benchmark_latest.json").exists())
            self.assertTrue((root / "routing" / "routing_benchmark_latest.json").exists())
            self.assertTrue((root / "rag" / "pdf_targeted" / "pdf_targeted_after_focus.json").exists())
            self.assertTrue((root / "rag" / "general" / "benchmark-results-20260327-232520.json").exists())
            self.assertTrue((root / "logs" / "backend-8035.log").exists())


if __name__ == "__main__":
    unittest.main()
