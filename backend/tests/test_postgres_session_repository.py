from __future__ import annotations

import os
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

from src.backend.runtime.postgres_session_repository import PostgresSessionRepository
from src.backend.runtime.session_manager import FsSessionRepository


TEST_POSTGRES_DSN = os.getenv("RAGCLAW_TEST_POSTGRES_DSN") or os.getenv("RAGCLAW_POSTGRES_DSN")
MIGRATIONS_DIR = PROJECT_ROOT / "backend" / "migrations"


@unittest.skipUnless(TEST_POSTGRES_DSN, "RAGCLAW_TEST_POSTGRES_DSN or RAGCLAW_POSTGRES_DSN is required")
class PostgresSessionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = PostgresSessionRepository(TEST_POSTGRES_DSN, migrations_dir=MIGRATIONS_DIR)
        self.repository.reset_all()

    def test_session_crud_archive_and_delete_roundtrip(self) -> None:
        session = self.repository.create_session("Postgres Session")
        session_id = session["id"]

        self.repository.save_message(session_id, "user", "hello", message_id="msg-user")
        self.repository.save_message(session_id, "assistant", "world", message_id="msg-assistant", turn_id="turn-1", run_id="run-1")
        self.repository.rename_session(session_id, "Renamed Session")
        archived = self.repository.archive_session(session_id)

        self.assertEqual(self.repository.list_sessions(), [])
        self.assertEqual(archived["title"], "Renamed Session")
        self.assertIsNotNone(archived["archived_at"])
        self.assertEqual(len(self.repository.load_session(session_id)), 2)

        self.repository.delete_session(session_id)
        self.assertEqual(self.repository.list_sessions(), [])
        recreated = self.repository.load_session_record(session_id)
        self.assertEqual(recreated["title"], "New Session")
        self.assertEqual(recreated["messages"], [])

    def test_import_from_filesystem_preserves_message_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fs_repository = FsSessionRepository(Path(temp_dir))
            session = fs_repository.create_session("Filesystem Session")
            session_id = session["id"]
            fs_repository.save_message(session_id, "user", "hello", message_id="fs-user")
            fs_repository.save_message(session_id, "assistant", "there", message_id="fs-assistant", turn_id="turn-1", run_id="run-1")
            fs_repository.exclude_turn_from_context(
                session_id=session_id,
                turn_id="turn-1",
                run_id="run-1",
                reason="test",
                created_at="2026-04-11T22:00:00Z",
            )

            report = self.repository.import_from_filesystem(Path(temp_dir) / "sessions")
            record = self.repository.load_session_record(session_id)

        self.assertEqual(report["imported_sessions"], 1)
        self.assertEqual(report["imported_messages"], 2)
        self.assertEqual(record["title"], "Filesystem Session")
        self.assertEqual(len(record["messages"]), 2)
        self.assertIn("turn-1", record["excluded_turn_ids"])


if __name__ == "__main__":
    unittest.main()
