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

from src.backend.api import runs as runs_api
from src.backend.observability.trace_store import JsonlRunTraceRepository
from src.backend.observability.types import HarnessEvent, RunMetadata, RunOutcome
from src.backend.runtime.backends import RuntimeBackendConfig, RuntimeBackends
from src.backend.runtime.policy import InMemoryQueueBackend
from src.backend.runtime.session_manager import SessionManager


class _FakeHitlRepository:
    @property
    def saver(self):
        return None

    def configure_for_base_dir(self, _base_dir):
        return None

    def close(self):
        return None

    def thread_id_for(self, session_id=None, run_id=None):
        return str(session_id or run_id or "")

    def list_thread_checkpoints(self, thread_id):
        return []

    def get_checkpoint(self, thread_id, checkpoint_id):
        return None

    def latest_checkpoint(self, thread_id):
        return None

    def pending_hitl(self, thread_id):
        return None

    def list_pending_hitl(self, limit=50):
        class _Pending:
            def to_dict(self_inner):
                return {"request_id": "hitl-1", "thread_id": "thread-1", "status": "pending"}

        return [_Pending()]

    def list_hitl_requests(self, thread_id):
        return []

    def get_hitl_request(self, **kwargs):
        return None

    def get_hitl_decision(self, **kwargs):
        return None

    def record_pending_hitl(self, request):
        return request, True

    def record_hitl_decision(self, **kwargs):
        return None


class RunsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.trace_repository = JsonlRunTraceRepository(self.base_dir / "storage" / "runs")
        self.session_repository = SessionManager(self.base_dir)
        self.hitl_repository = _FakeHitlRepository()
        metadata = RunMetadata(
            run_id="run-api-1",
            session_id="session-api",
            user_message="hello api",
            source="chat_api",
            started_at="2026-04-11T20:20:00Z",
            orchestration_engine="langgraph",
        )
        self.trace_repository.create_run(metadata)
        self.trace_repository.append_event(
            HarnessEvent(
                event_id="evt-api-1",
                run_id="run-api-1",
                name="run.started",
                ts="2026-04-11T20:20:00Z",
                payload={"session_id": "session-api"},
            )
        )
        self.trace_repository.finalize_run(
            "run-api-1",
            RunOutcome(
                status="completed",
                final_answer="api ok",
                route_intent="direct_answer",
                completed_at="2026-04-11T20:20:02Z",
                orchestration_engine="langgraph",
            ),
        )

        self.app = FastAPI()
        self.app.include_router(runs_api.router, prefix="/api")

        self._previous_runtime_backends = runs_api.agent_manager.runtime_backends
        self._previous_hitl_repository = runs_api.agent_manager.hitl_repository
        runs_api.agent_manager.runtime_backends = RuntimeBackends(
            config=RuntimeBackendConfig(),
            session_repository=self.session_repository,
            trace_repository=self.trace_repository,
            queue_backend=InMemoryQueueBackend(lambda: "2026-04-11T20:20:00Z"),
            hitl_repository=self.hitl_repository,
        )
        runs_api.agent_manager.hitl_repository = self.hitl_repository

    def tearDown(self) -> None:
        runs_api.agent_manager.runtime_backends = self._previous_runtime_backends
        runs_api.agent_manager.hitl_repository = self._previous_hitl_repository
        self._tmpdir.cleanup()

    def test_runs_api_lists_and_reads_runs(self) -> None:
        with TestClient(self.app) as client:
            listing = client.get("/api/runs")
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.json()["count"], 1)

            detail = client.get("/api/runs/run-api-1")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["trace"]["outcome"]["status"], "completed")

            events = client.get("/api/runs/run-api-1/events")
            self.assertEqual(events.status_code, 200)
            self.assertEqual(events.json()["count"], 1)

            stats = client.get("/api/runs/stats")
            self.assertEqual(stats.status_code, 200)
            self.assertEqual(stats.json()["total_runs"], 1)

            pending = client.get("/api/hitl/pending")
            self.assertEqual(pending.status_code, 200)
            self.assertEqual(pending.json()["count"], 1)


if __name__ == "__main__":
    unittest.main()
