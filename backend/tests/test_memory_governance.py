from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.context.episodic_memory import build_episodic_summary
from src.backend.context.governance import extract_memory_candidates
from src.backend.context.working_memory import build_working_memory


class MemoryGovernanceTests(unittest.TestCase):
    def test_extracts_typed_candidates_and_filters_forbidden_noise(self) -> None:
        state = {
            "run_id": "run-1",
            "session_id": "session-1",
            "thread_id": "session-1",
            "user_message": "I'm a product manager. Prefer concise grounded answers. Release freeze is 2026-05-01. Use https://notion.so/spec and knowledge/report.pdf.",
            "route_decision": SimpleNamespace(intent="knowledge_qa", subtype="compare"),
            "execution_strategy": SimpleNamespace(to_instructions=lambda: ["Prefer concise grounded answers.", "Avoid raw traces."]),
            "capability_results": [
                {"capability_id": "mcp_filesystem_read_file", "status": "success", "payload": {"path": "knowledge/report.pdf"}}
            ],
            "final_answer": "The release freeze is 2026-05-01.",
            "checkpoint_meta": {"updated_at": "2026-04-09T10:10:00Z"},
        }
        working_memory = build_working_memory(state, updated_at="2026-04-09T10:10:00Z")
        episodic_summary = build_episodic_summary(state, updated_at="2026-04-09T10:10:00Z")

        candidates = extract_memory_candidates(
            state=state,
            working_memory=working_memory,
            episodic_summary=episodic_summary,
            base_dir=PROJECT_ROOT / "backend",
            updated_at="2026-04-09T10:10:00Z",
        )

        types = {item.memory_type for item in candidates}
        self.assertIn("user_profile", types)
        self.assertIn("preference_feedback", types)
        self.assertIn("project_fact", types)
        self.assertIn("external_reference", types)
        self.assertIn("artifact_map", types)
        self.assertIn("workflow_rule", types)
        self.assertIn("session_episode", types)
        self.assertFalse(any("stack trace" in item.content.lower() for item in candidates))

    def test_forbidden_long_term_patterns_are_not_promoted(self) -> None:
        state = {
            "run_id": "run-2",
            "session_id": "session-2",
            "thread_id": "session-2",
            "user_message": "Release freeze is 2026-05-01 but this stack trace and raw checkpoint blob are just noisy tool output.",
            "route_decision": SimpleNamespace(intent="knowledge_qa", subtype="compare"),
            "checkpoint_meta": {"updated_at": "2026-04-09T10:12:00Z"},
        }
        working_memory = build_working_memory(state, updated_at="2026-04-09T10:12:00Z")
        episodic_summary = build_episodic_summary(state, updated_at="2026-04-09T10:12:00Z")

        candidates = extract_memory_candidates(
            state=state,
            working_memory=working_memory,
            episodic_summary=episodic_summary,
            base_dir=PROJECT_ROOT / "backend",
            updated_at="2026-04-09T10:12:00Z",
        )

        self.assertEqual([item for item in candidates if item.memory_type == "project_fact"], [])


if __name__ == "__main__":
    unittest.main()
