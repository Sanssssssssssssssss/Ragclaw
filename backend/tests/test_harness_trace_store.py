from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.backend.observability.trace_store import RunTraceStore
from src.backend.observability.types import HarnessEvent, RunMetadata, RunOutcome


class HarnessTraceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.store = RunTraceStore(self.root)
        self.metadata = RunMetadata(
            run_id="run-001",
            session_id="session-001",
            user_message="hello",
            source="chat_api",
            started_at="2026-04-02T13:00:00Z",
        )

    def test_create_run_writes_metadata_record(self) -> None:
        paths = self.store.create_run(self.metadata)
        self.assertTrue(paths.trace_path.exists())
        trace = self.store.read_trace(self.metadata.run_id)
        self.assertEqual(trace["metadata"]["run_id"], "run-001")
        self.assertEqual(trace["events"], [])
        self.assertIsNone(trace["outcome"])

    def test_append_event_persists_event_in_order(self) -> None:
        self.store.create_run(self.metadata)
        event = HarnessEvent(
            event_id="evt-1",
            run_id="run-001",
            name="run.started",
            ts="2026-04-02T13:00:01Z",
            payload={"route": "knowledge_qa"},
        )
        self.store.append_event(event)
        trace = self.store.read_trace("run-001")
        self.assertEqual(len(trace["events"]), 1)
        self.assertEqual(trace["events"][0]["name"], "run.started")

    def test_append_event_rejects_missing_trace(self) -> None:
        event = HarnessEvent(
            event_id="evt-1",
            run_id="missing-run",
            name="run.started",
            ts="2026-04-02T13:00:01Z",
            payload={},
        )
        with self.assertRaises(FileNotFoundError):
            self.store.append_event(event)

    def test_finalize_run_writes_summary_and_outcome(self) -> None:
        self.store.create_run(self.metadata)
        outcome = RunOutcome(
            status="completed",
            final_answer="done",
            route_intent="knowledge_qa",
            used_skill="",
            tool_names=("read_file",),
            retrieval_sources=("knowledge/report.pdf",),
            completed_at="2026-04-02T13:00:10Z",
        )
        paths = self.store.finalize_run("run-001", outcome)
        self.assertTrue(paths.summary_path.exists())
        trace = self.store.read_trace("run-001")
        self.assertEqual(trace["outcome"]["status"], "completed")

    def test_append_after_finalize_is_rejected(self) -> None:
        self.store.create_run(self.metadata)
        outcome = RunOutcome(
            status="completed",
            final_answer="done",
            route_intent="direct_answer",
            completed_at="2026-04-02T13:00:10Z",
        )
        self.store.finalize_run("run-001", outcome)
        event = HarnessEvent(
            event_id="evt-2",
            run_id="run-001",
            name="answer.completed",
            ts="2026-04-02T13:00:11Z",
            payload={"content": "done"},
        )
        with self.assertRaises(RuntimeError):
            self.store.append_event(event)


if __name__ == "__main__":
    unittest.main()
