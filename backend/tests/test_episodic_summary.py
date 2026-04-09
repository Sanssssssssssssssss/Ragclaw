from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.context.episodic_memory import build_episodic_summary


class EpisodicSummaryTests(unittest.TestCase):
    def test_build_summary_tracks_completed_rejected_and_open_loops(self) -> None:
        state = {
            "path_kind": "capability",
            "final_answer": "The web fetch failed, so I answered conservatively.",
            "approval_decision": "reject",
            "recovery_action": "fallback_to_answer",
            "capability_results": [
                {"capability_id": "mcp_web_fetch_url", "status": "failed", "error_type": "network_error"},
                {"capability_id": "mcp_filesystem_read_file", "status": "success"},
            ],
            "memory_retrieval": [{"source": "memory/MEMORY.md"}],
            "knowledge_retrieval": type("KnowledgeResult", (), {"evidences": [], "status": "partial"})(),
            "interrupt_request": {"capability_id": "python_repl"},
            "checkpoint_meta": {"updated_at": "2026-04-09T09:10:00Z"},
        }

        summary = build_episodic_summary(state, updated_at="2026-04-09T09:10:00Z")

        self.assertIn("The web fetch failed", summary.key_facts[0])
        self.assertIn("mcp_filesystem_read_file::success", summary.completed_subtasks)
        self.assertTrue(any("rejected" in item for item in summary.rejected_paths))
        self.assertTrue(any("recovery=fallback_to_answer" == item for item in summary.important_decisions))
        self.assertIn("memory/MEMORY.md", summary.important_artifacts)
        self.assertTrue(any("pending approval" in item for item in summary.open_loops))
        self.assertEqual(summary.updated_at, "2026-04-09T09:10:00Z")


if __name__ == "__main__":
    unittest.main()
