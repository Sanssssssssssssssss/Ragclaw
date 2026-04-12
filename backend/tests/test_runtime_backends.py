from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.observability.types import HarnessEvent, RunMetadata, RunOutcome
from src.backend.runtime.backends import build_runtime_backends
from src.backend.runtime.hitl_repository import SqliteHitlRepository
from src.backend.runtime.policy import InMemoryQueueBackend
from src.backend.runtime.postgres_session_repository import PostgresSessionRepository
from src.backend.runtime.runtime import HarnessRuntime, RuntimeDependencies
from src.backend.runtime.session_manager import FsSessionRepository, SessionManager
from src.backend.observability.trace_store import JsonlRunTraceRepository, RunTraceStore

TEST_POSTGRES_DSN = os.getenv("RAGCLAW_TEST_POSTGRES_DSN") or os.getenv("RAGCLAW_POSTGRES_DSN")


class _RecordingTraceRepository:
    def __init__(self) -> None:
        self.metadata = None
        self.events: list[HarnessEvent] = []
        self.outcome = None

    def create_run(self, metadata):
        self.metadata = metadata
        return object()

    def append_event(self, event):
        self.events.append(event)

    def finalize_run(self, run_id, outcome):
        self.outcome = outcome
        return object()

    def read_trace(self, run_id):
        return {
            "run_id": run_id,
            "metadata": self.metadata.to_dict() if self.metadata else None,
            "events": [event.to_dict() for event in self.events],
            "outcome": self.outcome.to_dict() if self.outcome else None,
        }


class _ImmediateQueueBackend:
    async def acquire(self, session_id, *, owner_id=None):
        class _Lease:
            def __init__(self, session_id_value):
                self.session_id = session_id_value
                self.lease_id = "lease-immediate"
                self.queued = False
                self.queued_at = None
                self.dequeued_at = None
                self.heartbeat_interval_seconds = None

            async def wait_until_active(self, _now_factory):
                return None

            async def heartbeat(self):
                return None

        return _Lease(session_id)

    async def is_active(self, lease):
        return True

    async def heartbeat(self, lease):
        return True

    async def release(self, lease_or_session):
        return None


class _Executor:
    async def execute(self, runtime, handle, *, message, history):
        await runtime.emit(handle, "route.decided", {"intent": "direct_answer", "needs_tools": False, "needs_retrieval": False})
        await runtime.emit(handle, "answer.completed", {"segment_index": 0, "content": "ok", "final": True})


class RuntimeBackendsTests(unittest.IsolatedAsyncioTestCase):
    async def test_harness_runtime_accepts_trace_and_queue_abstractions(self) -> None:
        runtime = HarnessRuntime(
            RuntimeDependencies(
                trace_store=_RecordingTraceRepository(),
                queue=_ImmediateQueueBackend(),
                now_factory=lambda: "2026-04-11T19:00:00Z",
                run_id_factory=lambda: "run-abstraction",
                event_id_factory=lambda: "evt-abstraction",
            )
        )

        events = [event async for event in runtime.run_with_executor(user_message="hello", session_id="session-1", executor=_Executor(), history=[])]
        self.assertEqual(events[-1].name, "run.completed")

    def test_build_runtime_backends_defaults_to_local_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {}, clear=False):
            backends = build_runtime_backends(Path(temp_dir), now_factory=lambda: "2026-04-11T19:00:00Z")
            backends.hitl_repository.close()

        self.assertEqual(backends.config.session_backend, "filesystem")
        self.assertEqual(backends.config.trace_backend, "jsonl")
        self.assertEqual(backends.config.queue_backend, "inmemory")
        self.assertEqual(backends.config.hitl_backend, "sqlite")
        self.assertIsInstance(backends.session_repository, FsSessionRepository)
        self.assertIsInstance(backends.trace_repository, JsonlRunTraceRepository)
        self.assertIsInstance(backends.queue_backend, InMemoryQueueBackend)
        self.assertIsInstance(backends.hitl_repository, SqliteHitlRepository)

    @unittest.skipUnless(TEST_POSTGRES_DSN, "RAGCLAW_TEST_POSTGRES_DSN or RAGCLAW_POSTGRES_DSN is required")
    def test_build_runtime_backends_supports_postgres_session_backend(self) -> None:
        env = {
            "RAGCLAW_SESSION_BACKEND": "postgres",
            "RAGCLAW_POSTGRES_DSN": TEST_POSTGRES_DSN,
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, env, clear=False):
            backends = build_runtime_backends(Path(temp_dir), now_factory=lambda: "2026-04-11T19:00:00Z")
            backends.hitl_repository.close()
        self.assertEqual(backends.config.session_backend, "postgres")
        self.assertIsInstance(backends.session_repository, PostgresSessionRepository)

    def test_session_manager_remains_compatible_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = SessionManager(Path(temp_dir))
            self.assertIsInstance(manager, FsSessionRepository)
            session = manager.create_session("Alias Test")
            manager.save_message(session["id"], "user", "hello")
            self.assertEqual(manager.load_session_for_agent(session["id"]), [{"role": "user", "content": "hello"}])

    def test_run_trace_store_remains_compatible_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunTraceStore(Path(temp_dir))
            metadata = RunMetadata(run_id="run-1", session_id="session-1", user_message="hello", started_at="2026-04-11T19:00:00Z")
            store.create_run(metadata)
            store.append_event(HarnessEvent(event_id="evt-1", run_id="run-1", name="run.started", ts="2026-04-11T19:00:01Z", payload={}))
            store.finalize_run("run-1", RunOutcome(status="completed", completed_at="2026-04-11T19:00:02Z"))
            trace = store.read_trace("run-1")
            self.assertEqual(trace["metadata"]["run_id"], "run-1")
            self.assertEqual(trace["outcome"]["status"], "completed")

    async def test_inmemory_queue_backend_serializes_same_session(self) -> None:
        queue = InMemoryQueueBackend(lambda: "2026-04-11T19:00:00Z")
        first = await queue.acquire("session-1")
        second = await queue.acquire("session-1")
        self.assertFalse(first.queued)
        self.assertTrue(second.queued)

        activated = False

        async def _wait_second():
            nonlocal activated
            await second.wait_until_active(lambda: "2026-04-11T19:00:01Z")
            activated = True

        waiter = asyncio.create_task(_wait_second())
        await asyncio.sleep(0.01)
        self.assertFalse(activated)
        await queue.release("session-1")
        await waiter
        self.assertTrue(activated)


if __name__ == "__main__":
    unittest.main()
