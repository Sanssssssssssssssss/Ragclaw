from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from harness.policy import SessionSerialQueue
from harness.runtime import HarnessRuntime, RuntimeDependencies
from harness.trace_store import RunTraceStore


class _Executor:
    def __init__(self, steps=None, error: Exception | None = None) -> None:
        self.steps = list(steps or [])
        self.error = error

    async def execute(self, runtime, handle, *, message, history):
        for name, payload in self.steps:
            await runtime.emit(handle, name, payload)
        if self.error is not None:
            raise self.error


class HarnessRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.trace_store = RunTraceStore(self.root / "runs")
        self._tick = 0

        def next_time() -> str:
            value = f"2026-04-03T10:00:{self._tick:02d}Z"
            self._tick += 1
            return value

        self.runtime = HarnessRuntime(
            RuntimeDependencies(
                trace_store=self.trace_store,
                queue=SessionSerialQueue(next_time),
                now_factory=next_time,
                run_id_factory=lambda: "run-fixed",
                event_id_factory=lambda: "evt-fixed",
            )
        )

    def test_begin_run_creates_trace_and_run_started_event(self) -> None:
        handle = self.runtime.begin_run(user_message="hello", session_id="session-1", source="chat_api")
        trace = self.trace_store.read_trace(handle.run_id)
        self.assertEqual(trace["metadata"]["run_id"], "run-fixed")
        self.assertEqual(trace["events"][0]["name"], "run.started")

    async def test_run_with_executor_completes_and_finalizes_trace(self) -> None:
        executor = _Executor(
            steps=[
                ("route.decided", {"intent": "direct_answer", "needs_tools": False, "needs_retrieval": False}),
                ("answer.started", {"segment_index": 0, "content": "", "final": False}),
                ("answer.delta", {"segment_index": 0, "content": "hello", "final": False}),
                ("answer.completed", {"segment_index": 0, "content": "hello", "final": True}),
            ]
        )
        events = [event async for event in self.runtime.run_with_executor(user_message="hello", executor=executor, history=[])]
        trace = self.trace_store.read_trace("run-fixed")
        self.assertEqual(events[-1].name, "run.completed")
        self.assertEqual(trace["outcome"]["status"], "completed")
        self.assertEqual(trace["outcome"]["final_answer"], "hello")

    async def test_run_with_executor_failure_finalizes_trace(self) -> None:
        executor = _Executor(
            steps=[
                ("route.decided", {"intent": "direct_answer", "needs_tools": False, "needs_retrieval": False}),
                ("answer.delta", {"segment_index": 0, "content": "partial", "final": False}),
            ],
            error=RuntimeError("boom"),
        )
        events = [
            event
            async for event in self.runtime.run_with_executor(
                user_message="hello",
                executor=executor,
                history=[],
                suppress_failures=True,
            )
        ]
        trace = self.trace_store.read_trace("run-fixed")
        self.assertEqual(events[-1].name, "run.failed")
        self.assertEqual(trace["outcome"]["status"], "failed")
        self.assertEqual(trace["outcome"]["error_message"], "boom")

    async def test_queue_events_are_emitted_for_second_same_session_run(self) -> None:
        queue = SessionSerialQueue(lambda: "2026-04-03T11:00:00Z")
        runtime = HarnessRuntime(
            RuntimeDependencies(
                trace_store=RunTraceStore(self.root / "queued-runs"),
                queue=queue,
                now_factory=lambda: "2026-04-03T11:00:00Z",
                run_id_factory=iter(["run-a", "run-b"]).__next__,
                event_id_factory=lambda: "evt-fixed",
            )
        )

        blocker = _Executor(
            steps=[
                ("route.decided", {"intent": "direct_answer", "needs_tools": False, "needs_retrieval": False}),
            ]
        )

        wait_started = False

        class LongExecutor:
            async def execute(self, runtime_obj, handle, *, message, history):
                nonlocal wait_started
                await runtime_obj.emit(
                    handle,
                    "route.decided",
                    {"intent": "direct_answer", "needs_tools": False, "needs_retrieval": False},
                )
                wait_started = True
                while wait_started:
                    await asyncio.sleep(0.01)

        long_executor = LongExecutor()

        async def consume_first():
            return [event async for event in runtime.run_with_executor(user_message="first", session_id="session-1", executor=long_executor, history=[], suppress_failures=True)]

        async def consume_second():
            return [event async for event in runtime.run_with_executor(user_message="second", session_id="session-1", executor=blocker, history=[], suppress_failures=True)]

        import asyncio

        task1 = asyncio.create_task(consume_first())
        while not wait_started:
            await asyncio.sleep(0.01)
        task2 = asyncio.create_task(consume_second())
        await asyncio.sleep(0.05)
        wait_started = False
        await task1
        second_events = await task2

        self.assertIn("run.queued", [event.name for event in second_events])
        self.assertIn("run.dequeued", [event.name for event in second_events])


if __name__ == "__main__":
    unittest.main()
