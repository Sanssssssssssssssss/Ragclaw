from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.context.store import context_store
from src.backend.context.writer import ContextWriter


class ContextWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)
        self.writer = ContextWriter(base_dir=self.base_dir)

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def test_snapshot_persists_thread_context_and_promotes_memories(self) -> None:
        state = {
            "run_id": "run-1",
            "session_id": "session-1",
            "thread_id": "session-1",
            "user_message": "Prefer grounded answers and keep using knowledge/report.pdf for comparisons.",
            "route_decision": SimpleNamespace(intent="knowledge_qa", subtype="compare"),
            "execution_strategy": SimpleNamespace(to_instructions=lambda: ["Prefer grounded answers.", "Use knowledge retrieval only."]),
            "capability_results": [
                {"capability_id": "mcp_filesystem_read_file", "status": "success", "payload": {"path": "knowledge/report.pdf"}}
            ],
            "knowledge_retrieval": SimpleNamespace(
                evidences=[SimpleNamespace(source_path="knowledge/report.pdf", locator="page 2", snippet="gross margin 12.4%")],
                entity_hints=["SAIC"],
                status="success",
                reason="",
            ),
            "final_answer": "The report indicates gross margin reached 12.4%.",
            "checkpoint_meta": {"updated_at": "2026-04-09T10:10:00Z"},
        }

        snapshot = self.writer.snapshot(state, updated_at="2026-04-09T10:10:00Z")
        thread_snapshot = context_store.get_thread_snapshot(thread_id="session-1")
        semantic = context_store.list_memories(kind="semantic", limit=10)
        procedural = context_store.list_memories(kind="procedural", limit=10)

        self.assertIn("working_memory", snapshot)
        self.assertIn("episodic_summary", snapshot)
        self.assertIsNotNone(thread_snapshot)
        self.assertEqual(thread_snapshot.thread_id, "session-1")  # type: ignore[union-attr]
        self.assertTrue(semantic)
        self.assertTrue(procedural)


if __name__ == "__main__":
    unittest.main()
