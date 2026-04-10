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

from src.backend.context.store import context_store


class MemoryConflictAndStaleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def test_conflicting_memory_is_flagged_and_older_peer_is_superseded(self) -> None:
        older = context_store.insert_memory(
            kind="semantic",
            namespace="project:backend",
            memory_type="project_fact",
            scope="project",
            title="Release freeze",
            content="fact: Release freeze is 2026-05-01\nwhy: prior fact\nvalidation_hint: confirm with PM",
            summary="Release freeze is 2026-05-01",
            body={"fact": "Release freeze is 2026-05-01", "why": "prior fact", "validation_hint": "confirm with PM"},
            source="unit_test",
            created_at="2026-04-01T09:00:00Z",
            fingerprint="freeze-old",
            confidence=0.7,
            direct_prompt=True,
            promotion_priority=80,
            conflict_key="release-freeze",
        )
        newer = context_store.insert_memory(
            kind="semantic",
            namespace="project:backend",
            memory_type="project_fact",
            scope="project",
            title="Release freeze",
            content="fact: Release freeze is 2026-05-03\nwhy: refreshed fact\nvalidation_hint: confirm with PM",
            summary="Release freeze is 2026-05-03",
            body={"fact": "Release freeze is 2026-05-03", "why": "refreshed fact", "validation_hint": "confirm with PM"},
            source="unit_test",
            created_at="2026-04-10T09:00:00Z",
            fingerprint="freeze-new",
            confidence=0.92,
            direct_prompt=True,
            promotion_priority=82,
            conflict_key="release-freeze",
        )

        refreshed_older = context_store.get_memory(memory_id=older.memory_id)
        refreshed_newer = context_store.get_memory(memory_id=newer.memory_id)

        self.assertEqual(refreshed_older.status, "superseded")  # type: ignore[union-attr]
        self.assertTrue(refreshed_older.conflict_flag)  # type: ignore[union-attr]
        self.assertTrue(refreshed_newer.conflict_flag)  # type: ignore[union-attr]
        self.assertIn(older.memory_id, refreshed_newer.supersedes)  # type: ignore[union-attr]

    def test_stale_memory_drops_out_of_preferred_recall(self) -> None:
        stale = context_store.insert_memory(
            kind="procedural",
            namespace="project:backend",
            memory_type="workflow_rule",
            scope="project",
            title="Old workflow rule",
            content="rule: Use legacy dashboard only\nwhy: old setup\nhow_to_apply: only if still verified",
            summary="Use legacy dashboard only",
            body={"rule": "Use legacy dashboard only", "why": "old setup", "how_to_apply": "only if still verified"},
            source="unit_test",
            created_at="2025-01-01T09:00:00Z",
            fingerprint="workflow-stale",
            confidence=0.8,
            direct_prompt=True,
            promotion_priority=70,
            stale_after="2025-01-02T09:00:00Z",
            conflict_key="workflow-stale",
        )

        manifest = context_store.get_memory(memory_id=stale.memory_id)
        search = context_store.search_memory_manifests(
            kind="procedural",
            namespaces=("project:backend",),
            query="legacy dashboard workflow",
            path_kind="capability_path",
            limit=5,
        )

        self.assertEqual(manifest.status, "stale")  # type: ignore[union-attr]
        self.assertTrue(all(item.memory_id != stale.memory_id or item.freshness == "stale" for item in search))


if __name__ == "__main__":
    unittest.main()
