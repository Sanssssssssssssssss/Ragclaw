from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.api import sessions as sessions_api
from src.backend.runtime.session_manager import FsSessionRepository


class SessionsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repository = FsSessionRepository(Path(self._tmpdir.name))
        self.app = FastAPI()
        self.app.include_router(sessions_api.router, prefix="/api")

        self._previous_session_manager = sessions_api.agent_manager.session_manager
        sessions_api.agent_manager.session_manager = self.repository

    def tearDown(self) -> None:
        sessions_api.agent_manager.session_manager = self._previous_session_manager
        self._tmpdir.cleanup()

    def test_create_read_update_archive_and_delete_session(self) -> None:
        with TestClient(self.app) as client:
            created = client.post("/api/sessions", json={"title": "API Session"})
            self.assertEqual(created.status_code, 200)
            session_id = created.json()["id"]

            self.repository.save_message(session_id, "user", "hello", message_id="msg-1")

            listing = client.get("/api/sessions")
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.json()[0]["title"], "API Session")

            renamed = client.put(f"/api/sessions/{session_id}", json={"title": "Renamed API Session"})
            self.assertEqual(renamed.status_code, 200)
            self.assertEqual(renamed.json()["title"], "Renamed API Session")

            history = client.get(f"/api/sessions/{session_id}/history")
            self.assertEqual(history.status_code, 200)
            self.assertEqual(len(history.json()["messages"]), 1)

            archived = client.post(f"/api/sessions/{session_id}/archive")
            self.assertEqual(archived.status_code, 200)
            self.assertTrue(archived.json()["archived_at"])

            deleted = client.delete(f"/api/sessions/{session_id}")
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(deleted.json(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
