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


class TurnExclusionIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)
        self.session_manager = SessionManager(self.base_dir)
        self.service = ContextQuarantineService(
            session_manager=self.session_manager,
            base_dir=self.base_dir,
            now_factory=lambda: "2026-04-09T15:00:00Z",
        )

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def test_excluding_same_turn_twice_is_idempotent(self) -> None:
        session_id = "session-idem"
        self.session_manager.save_message(session_id, "user", "Polluted request", message_id="user-1", run_id="run-idem")
        self.session_manager.save_message(session_id, "assistant", "Polluted answer", message_id="assistant-1", turn_id="run-idem:0", run_id="run-idem")
        context_store.record_context_turn_snapshot(
            ContextTurnSnapshot(
                turn_id="run-idem:0",
                session_id=session_id,
                run_id="run-idem",
                thread_id=session_id,
                assistant_message_id="assistant-1",
                segment_index=0,
                call_site="direct_answer",
                path_type="direct_answer",
                user_query="Polluted request",
                context_envelope=ContextEnvelope(system_block="[Context policy]", history_block="", working_memory_block="", episodic_block="", semantic_block="", procedural_block="", conversation_block="", artifact_block="", evidence_block=""),
                assembly_decision=ContextAssemblyDecision(path_type="direct_answer"),
                created_at="2026-04-09T14:45:00Z",
            )
        )
        context_store.insert_memory(
            kind="semantic",
            namespace="project:test",
            title="Polluted memory",
            content="Polluted answer",
            summary="Polluted answer",
            source="assistant_message",
            created_at="2026-04-09T14:45:00Z",
            fingerprint="fp-idem",
            memory_type="project_fact",
            scope="project",
            source_turn_ids=("run-idem:0",),
            source_run_ids=("run-idem",),
            generated_by="context_writer",
            generated_at="2026-04-09T14:45:00Z",
        )

        first = self.service.exclude_turn(session_id=session_id, turn_id="run-idem:0")
        second = self.service.exclude_turn(session_id=session_id, turn_id="run-idem:0")
        record = self.session_manager.load_session_record(session_id)
        events = context_store.list_context_events(thread_id=session_id, limit=40)

        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(
            len([item for item in record.get("turn_actions", []) if item.get("action") == "exclude"]),
            1,
        )
        self.assertEqual(len([item for item in events if item.event_type == "context.turn_excluded"]), 1)


if __name__ == "__main__":
    unittest.main()
