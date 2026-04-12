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

from src.backend.observability.dual_trace_store import DualWriteRunTraceRepository
from src.backend.observability.postgres_trace_store import PostgresRunTraceRepository
from src.backend.observability.trace_store import JsonlRunTraceRepository
from src.backend.observability.types import HarnessEvent, RunMetadata, RunOutcome


TEST_POSTGRES_DSN = os.getenv("RAGCLAW_TEST_POSTGRES_DSN") or os.getenv("RAGCLAW_POSTGRES_DSN")
MIGRATIONS_DIR = PROJECT_ROOT / "backend" / "migrations"


@unittest.skipUnless(TEST_POSTGRES_DSN, "RAGCLAW_TEST_POSTGRES_DSN or RAGCLAW_POSTGRES_DSN is required")
class PostgresTraceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.postgres = PostgresRunTraceRepository(TEST_POSTGRES_DSN, migrations_dir=MIGRATIONS_DIR)
        self.postgres.reset_all()

    def test_postgres_trace_store_roundtrip(self) -> None:
        metadata = RunMetadata(
            run_id="run-pg-1",
            session_id="session-pg",
            thread_id="thread-pg",
            user_message="hello postgres",
            source="chat_api",
            started_at="2026-04-11T20:10:00Z",
            orchestration_engine="langgraph",
        )
        self.postgres.create_run(metadata)
        self.postgres.append_event(
            HarnessEvent(
                event_id="evt-1",
                run_id="run-pg-1",
                name="run.started",
                ts="2026-04-11T20:10:00Z",
                payload={"session_id": "session-pg"},
            )
        )
        self.postgres.append_event(
            HarnessEvent(
                event_id="evt-2",
                run_id="run-pg-1",
                name="answer.completed",
                ts="2026-04-11T20:10:01Z",
                payload={"content": "done", "final": True},
            )
        )
        self.postgres.finalize_run(
            "run-pg-1",
            RunOutcome(
                status="completed",
                final_answer="done",
                route_intent="direct_answer",
                completed_at="2026-04-11T20:10:02Z",
                orchestration_engine="langgraph",
            ),
        )

        trace = self.postgres.read_trace("run-pg-1")
        self.assertEqual(trace["metadata"]["run_id"], "run-pg-1")
        self.assertEqual(trace["outcome"]["status"], "completed")
        self.assertEqual(len(trace["events"]), 2)
        self.assertEqual(self.postgres.stats()["total_runs"], 1)
        self.assertEqual(len(self.postgres.list_runs()), 1)
        self.assertEqual(len(self.postgres.list_run_events("run-pg-1")), 2)

    def test_dual_write_trace_store_records_parity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dual = DualWriteRunTraceRepository(
                jsonl_repository=JsonlRunTraceRepository(Path(temp_dir) / "runs"),
                postgres_repository=self.postgres,
            )
            metadata = RunMetadata(
                run_id="run-dual-1",
                session_id="session-dual",
                user_message="dual write",
                source="chat_api",
                started_at="2026-04-11T20:11:00Z",
            )
            dual.create_run(metadata)
            dual.append_event(
                HarnessEvent(
                    event_id="evt-dual-1",
                    run_id="run-dual-1",
                    name="run.started",
                    ts="2026-04-11T20:11:00Z",
                    payload={"session_id": "session-dual"},
                )
            )
            dual.append_event(
                HarnessEvent(
                    event_id="evt-dual-2",
                    run_id="run-dual-1",
                    name="route.decided",
                    ts="2026-04-11T20:11:01Z",
                    payload={"intent": "direct_answer", "needs_tools": False, "needs_retrieval": False},
                )
            )
            dual.finalize_run(
                "run-dual-1",
                RunOutcome(
                    status="completed",
                    final_answer="dual ok",
                    route_intent="direct_answer",
                    completed_at="2026-04-11T20:11:02Z",
                ),
            )

            parity = dual.parity_report("run-dual-1")
            self.assertIsNotNone(parity)
            self.assertEqual(parity["jsonl_event_count"], 2)
            self.assertEqual(parity["postgres_event_count"], 2)
            self.assertTrue(parity["ordering_match"])

    def test_dual_write_ignores_duplicate_event_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dual = DualWriteRunTraceRepository(
                jsonl_repository=JsonlRunTraceRepository(Path(temp_dir) / "runs"),
                postgres_repository=self.postgres,
            )
            metadata = RunMetadata(
                run_id="run-dual-retry",
                session_id="session-dual",
                user_message="dual retry",
                source="chat_api",
                started_at="2026-04-11T20:11:00Z",
            )
            dual.create_run(metadata)
            event = HarnessEvent(
                event_id="evt-retry-1",
                run_id="run-dual-retry",
                name="run.started",
                ts="2026-04-11T20:11:00Z",
                payload={"session_id": "session-dual"},
            )
            dual.append_event(event)
            dual.append_event(event)
            dual.finalize_run(
                "run-dual-retry",
                RunOutcome(
                    status="completed",
                    final_answer="dual ok",
                    route_intent="direct_answer",
                    completed_at="2026-04-11T20:11:02Z",
                ),
            )

            parity = dual.parity_report("run-dual-retry")
            trace = dual.read_trace("run-dual-retry")
            self.assertEqual(trace["event_count"], 1)
            self.assertIsNotNone(parity)
            self.assertEqual(parity["jsonl_event_count"], 1)
            self.assertEqual(parity["postgres_event_count"], 1)
            self.assertTrue(parity["ordering_match"])


if __name__ == "__main__":
    unittest.main()
