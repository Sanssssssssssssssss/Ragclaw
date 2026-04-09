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

from src.backend.context.procedural_memory import procedural_memory
from src.backend.context.store import context_store


class ProceduralMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        base_dir = Path(self._tmpdir.name) / "backend"
        base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(base_dir)

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def test_insert_list_and_search_procedural_memory(self) -> None:
        record = procedural_memory.insert(
            namespace="user:default",
            title="User preference",
            content="Prefer grounded answers and avoid raw trace details.",
            summary="Grounded answers only.",
            tags=("preference", "grounded"),
            metadata={"source": "unit_test"},
            source="unit_test",
            created_at="2026-04-09T10:05:00Z",
            fingerprint="procedural-test-1",
        )

        listed = procedural_memory.list(namespace="user:default", limit=5)
        searched = procedural_memory.search(
            namespaces=("user:default",),
            query="avoid raw trace",
            limit=5,
        )

        self.assertEqual(record.kind, "procedural")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].memory_id, record.memory_id)
        self.assertEqual(len(searched), 1)
        self.assertEqual(searched[0].memory_id, record.memory_id)


if __name__ == "__main__":
    unittest.main()
