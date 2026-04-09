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

from src.backend.context.working_memory import build_working_memory


class WorkingMemoryTests(unittest.TestCase):
    def test_build_working_memory_collects_recent_state(self) -> None:
        state = {
            "thread_id": "session-1",
            "run_id": "run-1",
            "user_message": "Compare SAIC and BYD results in knowledge/report.pdf and keep Python REPL out.",
            "route_decision": SimpleNamespace(intent="knowledge_qa", subtype="compare"),
            "execution_strategy": SimpleNamespace(
                to_instructions=lambda: [
                    "Do not call python_repl.",
                    "Use knowledge retrieval only.",
                ]
            ),
            "memory_retrieval": [{"source": "memory/MEMORY.md", "text": "Prior comparison request"}],
            "knowledge_retrieval": SimpleNamespace(
                evidences=[SimpleNamespace(source_path="knowledge/report.pdf", locator="page 2", snippet="BYD margin 12%")],
                entity_hints=["SAIC", "BYD"],
                status="partial",
                reason="only one quarter available",
            ),
            "capability_results": [
                {
                    "capability_id": "mcp_web_fetch_url",
                    "status": "success",
                    "payload": {"text": "example"},
                    "error_type": "",
                }
            ],
            "interrupt_request": {"capability_id": "python_repl"},
            "recovery_action": "retry_once",
            "checkpoint_meta": {"updated_at": "2026-04-09T09:00:00Z"},
        }

        memory = build_working_memory(state, updated_at="2026-04-09T09:00:00Z")

        self.assertEqual(memory.thread_id, "session-1")
        self.assertEqual(memory.current_goal, state["user_message"])
        self.assertIn("knowledge_qa", memory.latest_user_intent)
        self.assertIn("Do not call python_repl.", memory.active_constraints)
        self.assertIn("SAIC", memory.active_entities)
        self.assertIn("knowledge/report.pdf", memory.active_artifacts)
        self.assertTrue(memory.latest_capability_results)
        self.assertIn("knowledge evidence is partial", memory.unresolved_items)
        self.assertEqual(memory.updated_at, "2026-04-09T09:00:00Z")


if __name__ == "__main__":
    unittest.main()
