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

from src.backend.context.recall import conversation_recall
from src.backend.context.store import context_store


class ConversationRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def test_thread_recall_returns_relevant_history_chunks(self) -> None:
        records = conversation_recall.record(
            state={
                "run_id": "run-1",
                "session_id": "session-1",
                "thread_id": "session-1",
                "history": [
                    {"role": "user", "content": "Earlier we discussed the release freeze deadline."},
                    {"role": "assistant", "content": "The release freeze starts on 2026-05-01."},
                    {"role": "user", "content": "Remind me about that release freeze later."},
                ],
            },
            updated_at="2026-04-09T10:10:00Z",
        )

        hits = conversation_recall.retrieve(thread_id="session-1", query="release freeze", limit=2)

        self.assertTrue(records)
        self.assertTrue(hits)
        self.assertIn("release freeze", (hits[0].summary or hits[0].snippet).lower())


if __name__ == "__main__":
    unittest.main()
