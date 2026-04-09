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
from src.backend.context.quarantine import ContextQuarantineService
from src.backend.context.store import context_store
from src.backend.runtime.session_manager import SessionManager


class ContextRebuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)
        self.session_manager = SessionManager(self.base_dir)
        self.service = ContextQuarantineService(
            session_manager=self.session_manager,
            base_dir=self.base_dir,
            now_factory=lambda: "2026-04-09T14:00:00Z",
        )

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def _record_turn(self, *, session_id: str, run_id: str, turn_id: str, user_query: str, answer: str, created_at: str) -> None:
        self.session_manager.save_message(session_id, "user", user_query, message_id=f"{turn_id}:user", run_id=run_id)
        self.session_manager.save_message(
            session_id,
            "assistant",
            answer,
            message_id=f"{turn_id}:assistant",
            turn_id=turn_id,
            run_id=run_id,
        )
        context_store.record_context_turn_snapshot(
            ContextTurnSnapshot(
                turn_id=turn_id,
                session_id=session_id,
                run_id=run_id,
                thread_id=session_id,
                assistant_message_id=f"{turn_id}:assistant",
                segment_index=int(turn_id.rsplit(":", 1)[-1]),
                call_site="direct_answer",
                path_type="direct_answer",
                user_query=user_query,
                context_envelope=ContextEnvelope(system_block="[Context policy]", history_block="[Recent history]", working_memory_block="", episodic_block="", semantic_block="", procedural_block="", conversation_block="", artifact_block="", evidence_block=""),
                assembly_decision=ContextAssemblyDecision(path_type="direct_answer"),
                created_at=created_at,
            )
        )

    def test_exclude_rebuilds_thread_snapshot_and_recall_from_remaining_turns(self) -> None:
        session_id = "session-rebuild"
        self._record_turn(
            session_id=session_id,
            run_id="run-bad",
            turn_id="run-bad:0",
            user_query="Bad question",
            answer="Bad answer",
            created_at="2026-04-09T13:00:00Z",
        )
        self._record_turn(
            session_id=session_id,
            run_id="run-good",
            turn_id="run-good:0",
            user_query="Safe question",
            answer="Safe grounded answer",
            created_at="2026-04-09T13:30:00Z",
        )

        result = self.service.exclude_turn(session_id=session_id, turn_id="run-bad:0")

        snapshot = context_store.get_thread_snapshot(thread_id=session_id)
        recall_chunks = context_store.list_conversation_chunks(thread_id=session_id, limit=20)

        self.assertTrue(result.changed)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.working_memory["current_goal"], "Safe question")  # type: ignore[union-attr,index]
        self.assertIn("Safe grounded answer", " ".join(snapshot.episodic_summary.get("key_facts", [])))  # type: ignore[union-attr]
        self.assertTrue(recall_chunks)
        self.assertTrue(all("run-bad:0" not in item.source_turn_ids for item in recall_chunks))
        self.assertTrue(any("run-good:0" in item.source_turn_ids for item in recall_chunks))


if __name__ == "__main__":
    unittest.main()
