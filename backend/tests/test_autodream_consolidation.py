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

from src.backend.context.consolidation import AutoDreamConsolidator
from src.backend.context.store import context_store


class AutoDreamConsolidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def test_consolidation_promotes_stable_candidates_and_writes_memory_index(self) -> None:
        stable_candidate = {
            "memory_type": "workflow_rule",
            "title": "Grounded workflow rule",
            "summary": "Always answer with grounded evidence.",
            "namespace": "project:backend",
            "fingerprint": "workflow-grounded",
            "conflict_key": "workflow-grounded",
            "confidence": 0.88,
            "direct_prompt": True,
        }
        for index in range(2):
            context_store.insert_memory(
                kind="episodic",
                namespace="thread:session-1",
                memory_type="session_episode",
                scope="thread",
                title=f"Session episode {index}",
                content="Grounded workflow rule appeared again.",
                summary="Grounded workflow rule appeared again.",
                tags=("episode",),
                metadata={"stable_candidates": [stable_candidate]},
                source="unit_test",
                created_at=f"2026-04-09T10:0{index}:00Z",
                fingerprint=f"episode-{index}",
                confidence=0.8,
                direct_prompt=False,
                conflict_key=f"episode-{index}",
            )

        consolidator = AutoDreamConsolidator(base_dir=self.base_dir)
        result = consolidator.consolidate(
            trigger="manual",
            thread_id="thread:session-1",
            started_at="2026-04-09T10:10:00Z",
            force=True,
        )

        promoted = context_store.get_memory_by_fingerprint(fingerprint="workflow-grounded")
        memory_index_path = self.base_dir.parent / "memory" / "MEMORY.md"

        self.assertEqual(result.status, "completed")
        self.assertTrue(result.promoted_memory_ids)
        self.assertIsNotNone(promoted)
        self.assertEqual(promoted.memory_type, "workflow_rule")  # type: ignore[union-attr]
        self.assertTrue(memory_index_path.exists())


if __name__ == "__main__":
    unittest.main()
