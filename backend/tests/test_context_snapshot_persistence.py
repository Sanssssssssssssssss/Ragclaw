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

from src.backend.context.models import ContextAssemblyDecision, ContextEnvelope, ContextTurnSnapshot
from src.backend.context.store import context_store


class ContextSnapshotPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def test_context_turn_snapshot_round_trips_with_blocks_and_budget(self) -> None:
        snapshot = ContextTurnSnapshot(
            turn_id="run-1:0",
            session_id="session-1",
            run_id="run-1",
            thread_id="thread-1",
            assistant_message_id=None,
            segment_index=0,
            call_site="direct_answer",
            path_type="direct_answer",
            user_query="Summarize the report.",
            context_envelope=ContextEnvelope(
                system_block="[Context policy]\nAnswer directly.",
                history_block="[Recent history]\nuser: hi",
                working_memory_block="[Working memory]\ncurrent_goal: summarize",
                episodic_block="[Episodic summary]\nkey_facts: report loaded",
                semantic_block="[Semantic memory]\n- Report facts",
                procedural_block="[Procedural memory]\n- Stay grounded",
                conversation_block="[Conversation recall]\n- user: remind me later",
                artifact_block="[Capability outputs]\n1. file read success",
                evidence_block="[Retrieval evidence]\n1. report.pdf|page 2",
                budget_report={"recent_history": 120, "semantic_memory": 80},
            ),
            assembly_decision=ContextAssemblyDecision(
                path_type="direct_answer",
                selected_history_ids=("history:1",),
                selected_memory_ids=("mem-1", "mem-2"),
                selected_artifact_ids=("artifact-1",),
                selected_evidence_ids=("report.pdf|page 2",),
                selected_conversation_ids=("conv-1",),
                dropped_items=("history:0",),
                truncation_reason="recent_history budget",
            ),
            budget_report={
                "allocated": {"recent_history": 320, "semantic_memory": 160},
                "used": {"recent_history": 120, "semantic_memory": 80},
                "excluded_from_prompt": ["raw trace events"],
            },
            selected_memory_ids=("mem-1", "mem-2"),
            selected_artifact_ids=("artifact-1",),
            selected_evidence_ids=("report.pdf|page 2",),
            selected_conversation_ids=("conv-1",),
            dropped_items=("history:0",),
            truncation_reason="recent_history budget",
            run_status="fresh",
            resume_source="",
            checkpoint_id="",
            orchestration_engine="langgraph",
            model_invoked=True,
            created_at="2026-04-09T10:00:00Z",
        )

        context_store.record_context_turn_snapshot(snapshot)

        listed = context_store.list_context_turn_snapshots(session_id="session-1", thread_id="thread-1", limit=5)
        loaded = context_store.get_context_turn_snapshot(turn_id="run-1:0", session_id="session-1")

        self.assertEqual(len(listed), 1)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.turn_id, "run-1:0")  # type: ignore[union-attr]
        self.assertEqual(loaded.context_envelope.semantic_block, "[Semantic memory]\n- Report facts")  # type: ignore[union-attr]
        self.assertEqual(loaded.assembly_decision.selected_memory_ids, ("mem-1", "mem-2"))  # type: ignore[union-attr]
        self.assertEqual(loaded.budget_report["used"]["semantic_memory"], 80)  # type: ignore[index,union-attr]


if __name__ == "__main__":
    unittest.main()
