from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.api import app as backend_app
from src.backend.observability.metrics import metrics_payload, set_pending_hitl
from src.backend.observability.trace_store import JsonlRunTraceRepository
from src.backend.runtime.policy import SessionSerialQueue
from src.backend.runtime.runtime import HarnessRuntime, RuntimeDependencies


def _metric_value(payload: str, name: str) -> float:
    match = re.search(rf"^{re.escape(name)}\s+([0-9.eE+-]+)$", payload, re.MULTILINE)
    if not match:
        raise AssertionError(f"metric not found: {name}")
    return float(match.group(1))


class _Executor:
    async def execute(self, runtime, handle, *, message, history):
        await runtime.emit(handle, "route.decided", {"intent": "direct_answer", "needs_tools": False, "needs_retrieval": False})
        await runtime.emit(handle, "retrieval.completed", {"results": []})
        await runtime.emit(handle, "answer.completed", {"segment_index": 0, "content": "ok", "final": True})


class MetricsApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_events_increment_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = HarnessRuntime(
                RuntimeDependencies(
                    trace_store=JsonlRunTraceRepository(Path(temp_dir) / "runs"),
                    queue=SessionSerialQueue(lambda: "2026-04-11T21:00:00Z"),
                    now_factory=lambda: "2026-04-11T21:00:00Z",
                    run_id_factory=lambda: "run-metrics",
                    event_id_factory=iter(["evt-1", "evt-2", "evt-3", "evt-4", "evt-5"]).__next__,
                )
            )

            before = metrics_payload().decode("utf-8")
            started_before = _metric_value(before, "ragclaw_runs_started_total")
            completed_before = _metric_value(before, "ragclaw_runs_completed_total")
            retrieval_before = _metric_value(before, "ragclaw_retrieval_calls_total")

            _ = [event async for event in runtime.run_with_executor(user_message="hello", session_id="session-metrics", executor=_Executor(), history=[])]

            after = metrics_payload().decode("utf-8")
            self.assertGreaterEqual(_metric_value(after, "ragclaw_runs_started_total"), started_before + 1)
            self.assertGreaterEqual(_metric_value(after, "ragclaw_runs_completed_total"), completed_before + 1)
            self.assertGreaterEqual(_metric_value(after, "ragclaw_retrieval_calls_total"), retrieval_before + 1)

    def test_metrics_endpoint_is_scrapeable(self) -> None:
        previous_hitl_repository = backend_app.agent_manager.hitl_repository

        class _HitlRepository:
            def list_pending_hitl(self, limit=500):
                return [object(), object()]

        backend_app.agent_manager.hitl_repository = _HitlRepository()
        try:
            set_pending_hitl(2)
            with (
                patch.object(backend_app, "refresh_snapshot"),
                patch.object(backend_app.agent_manager, "initialize"),
                patch.object(backend_app.memory_indexer, "configure"),
                patch.object(backend_app.memory_indexer, "rebuild_index"),
                patch.object(backend_app.knowledge_indexer, "configure"),
                patch.object(backend_app, "_schedule_knowledge_warm_start"),
            ):
                with TestClient(backend_app.app) as client:
                    response = client.get("/metrics")
            self.assertEqual(response.status_code, 200)
            self.assertIn("ragclaw_runs_started_total", response.text)
            self.assertIn("ragclaw_pending_hitl", response.text)
        finally:
            backend_app.agent_manager.hitl_repository = previous_hitl_repository


if __name__ == "__main__":
    unittest.main()
