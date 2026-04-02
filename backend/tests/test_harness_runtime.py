from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from harness.runtime import HarnessRuntime, RuntimeDependencies
from harness.trace_store import RunTraceStore


class HarnessRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.trace_store = RunTraceStore(self.root / "runs")
        self.runtime = HarnessRuntime(
            RuntimeDependencies(
                trace_store=self.trace_store,
                now_factory=lambda: "2026-04-02T14:00:00Z",
                run_id_factory=lambda: "run-fixed",
                event_id_factory=lambda: "evt-fixed",
            )
        )

    def test_begin_run_creates_trace_and_run_started_event(self) -> None:
        handle = self.runtime.begin_run(
            user_message="hello",
            session_id="session-1",
            source="chat_api",
        )
        trace = self.trace_store.read_trace(handle.run_id)
        self.assertEqual(trace["metadata"]["run_id"], "run-fixed")
        self.assertEqual(trace["events"][0]["name"], "run.started")

    def test_complete_run_finalizes_trace(self) -> None:
        handle = self.runtime.begin_run(user_message="hello")
        self.runtime.record_event(handle.run_id, "route.decided", {"intent": "knowledge_qa"})
        outcome = self.runtime.complete_run(
            handle.run_id,
            final_answer="done",
            route_intent="knowledge_qa",
            retrieval_sources=("knowledge/report.pdf",),
        )
        trace = self.trace_store.read_trace(handle.run_id)
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(trace["events"][-1]["name"], "run.completed")
        self.assertEqual(trace["outcome"]["status"], "completed")

    def test_fail_run_finalizes_trace_with_error(self) -> None:
        handle = self.runtime.begin_run(user_message="hello")
        outcome = self.runtime.fail_run(handle.run_id, error_message="boom", route_intent="tool")
        trace = self.trace_store.read_trace(handle.run_id)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(trace["events"][-1]["name"], "run.failed")
        self.assertEqual(trace["outcome"]["error_message"], "boom")

    async def test_run_with_executor_wraps_existing_executor_shape(self) -> None:
        async def fake_executor(handle, runtime):
            runtime.record_event(handle.run_id, "answer.started", {"segment_index": 0})
            runtime.record_event(handle.run_id, "answer.delta", {"content": "hello"})
            runtime.complete_run(handle.run_id, final_answer="hello", route_intent="direct_answer")
            return {"ok": True}

        handle, result = await self.runtime.run_with_executor(
            user_message="hello",
            executor=fake_executor,
        )
        trace = self.trace_store.read_trace(handle.run_id)
        self.assertTrue(result["ok"])
        self.assertEqual(trace["events"][1]["name"], "answer.started")
        self.assertEqual(trace["outcome"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()

