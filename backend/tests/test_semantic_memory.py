from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.context.semantic_memory import semantic_memory
from src.backend.context.store import context_store


class SemanticMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        base_dir = Path(self._tmpdir.name) / "backend"
        base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(base_dir)

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def test_insert_list_and_search_semantic_memory(self) -> None:
        record = semantic_memory.insert(
            namespace="thread:session-1",
            title="Project fact",
            content="The project uses LangGraph orchestration with checkpointed execution.",
            summary="LangGraph orchestration is enabled.",
            tags=("project", "langgraph"),
            metadata={"thread_id": "session-1"},
            source="unit_test",
            created_at="2026-04-09T10:00:00Z",
            fingerprint="semantic-test-1",
        )

        listed = semantic_memory.list(namespace="thread:session-1", limit=5)
        searched = semantic_memory.search(
            namespaces=("thread:session-1",),
            query="checkpointed execution",
            limit=5,
        )

        self.assertEqual(record.kind, "semantic")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].memory_id, record.memory_id)
        self.assertEqual(len(searched), 1)
        self.assertEqual(searched[0].memory_id, record.memory_id)


if __name__ == "__main__":
    unittest.main()
