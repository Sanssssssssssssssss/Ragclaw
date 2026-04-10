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
from src.backend.context.writer import ContextWriter


class SessionMemoryGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)
        self.writer = ContextWriter(base_dir=self.base_dir)

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def test_writer_skips_low_signal_session_memory_update_until_stable_pause(self) -> None:
        low_signal_state = {
            "run_id": "run-gate-1",
            "session_id": "session-gate",
            "thread_id": "session-gate",
            "user_message": "ping",
            "history": [],
            "checkpoint_meta": {"updated_at": "2026-04-10T10:00:00Z"},
        }

        skipped = self.writer.snapshot(low_signal_state, updated_at="2026-04-10T10:00:00Z")
        self.assertEqual(skipped["session_memory_state"]["last_decision"], "skip")
        self.assertIsNone(context_store.get_thread_snapshot(thread_id="session-gate"))

        stable_state = {
            "run_id": "run-gate-1",
            "session_id": "session-gate",
            "thread_id": "session-gate",
            "user_message": (
                "Please keep future answers grounded, concise, and tied to the release freeze discussion. "
                "We are working on the same project and this should stay true across sessions."
            ),
            "history": [
                {"role": "user", "content": "We need a stable summary for this project discussion."},
                {"role": "assistant", "content": "I will keep the answer grounded and concise."},
            ],
            "capability_results": [{"capability_id": "mcp_filesystem_read_file", "status": "success"}],
            "final_answer": "I will keep future answers grounded and concise around the release discussion.",
            "checkpoint_meta": {"updated_at": "2026-04-10T10:05:00Z"},
        }

        updated = self.writer.snapshot(stable_state, updated_at="2026-04-10T10:05:00Z")
        stored = context_store.get_thread_snapshot(thread_id="session-gate")

        self.assertIsNotNone(stored)
        self.assertEqual(updated["session_memory_state"]["last_decision"], "update")
        self.assertEqual(updated["session_memory_state"]["last_update_reason"], "initial_session_memory")
        self.assertEqual(stored.session_memory_state["last_update_reason"], "initial_session_memory")  # type: ignore[union-attr,index]

    def test_hitl_and_recovery_force_session_memory_update(self) -> None:
        baseline_state = {
            "run_id": "run-gate-2",
            "session_id": "session-gate-2",
            "thread_id": "session-gate-2",
            "user_message": "Keep the workflow grounded and explain tradeoffs in future runs.",
            "history": [
                {"role": "user", "content": "Keep the workflow grounded."},
                {"role": "assistant", "content": "I will keep the workflow grounded."},
            ],
            "capability_results": [{"capability_id": "mcp_web_fetch_url", "status": "success"}],
            "final_answer": "Grounded workflow noted for future runs.",
            "checkpoint_meta": {"updated_at": "2026-04-10T11:00:00Z"},
        }
        self.writer.snapshot(baseline_state, updated_at="2026-04-10T11:00:00Z")

        hitl_state = {
            **baseline_state,
            "approval_decision": "edit",
            "final_answer": "",
            "checkpoint_meta": {"updated_at": "2026-04-10T11:01:00Z"},
        }
        hitl_payload = self.writer.snapshot(hitl_state, updated_at="2026-04-10T11:01:00Z")
        self.assertEqual(hitl_payload["session_memory_state"]["last_update_reason"], "hitl_edit")

        recovery_state = {
            **baseline_state,
            "approval_decision": "",
            "recovery_action": "fallback_to_answer",
            "final_answer": "",
            "checkpoint_meta": {"updated_at": "2026-04-10T11:02:00Z"},
        }
        recovery_payload = self.writer.snapshot(recovery_state, updated_at="2026-04-10T11:02:00Z")
        self.assertEqual(recovery_payload["session_memory_state"]["last_update_reason"], "recovery_complete")


if __name__ == "__main__":
    unittest.main()
