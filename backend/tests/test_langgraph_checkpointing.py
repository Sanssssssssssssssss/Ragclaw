from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.orchestration.checkpointing import LangGraphCheckpointStore, PendingHitlRequest


class _State(TypedDict, total=False):
    run_id: str
    session_id: str | None
    user_message: str
    final_answer: str


class LangGraphCheckpointingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "storage" / "langgraph" / "checkpoints.sqlite"

    def _build_interrupt_graph(self, store: LangGraphCheckpointStore):
        graph = StateGraph(_State)
        graph.add_node(
            "step_one",
            lambda state: {
                "run_id": state["run_id"],
                "session_id": state["session_id"],
                "user_message": state["user_message"],
            },
        )

        def _approval_node(state: _State):
            response = interrupt({"decision": "pending"})
            return {"final_answer": f"decision:{response['decision']}"}

        graph.add_node("approval", _approval_node)
        graph.add_edge(START, "step_one")
        graph.add_edge("step_one", "approval")
        graph.add_edge("approval", END)
        return graph.compile(checkpointer=store.saver)

    def test_durable_checkpointer_persists_across_store_instances_and_resumes(self) -> None:
        store = LangGraphCheckpointStore(self.db_path)
        self.addCleanup(store.close)
        graph = self._build_interrupt_graph(store)
        thread_id = store.thread_id_for(session_id="session-1", run_id="run-1")

        initial_result = graph.invoke(
            {"run_id": "run-1", "session_id": "session-1", "user_message": "hello"},
            config={"configurable": {"thread_id": thread_id}},
        )
        self.assertTrue(initial_result.get("__interrupt__"))

        first_instance_checkpoints = store.list_thread_checkpoints(thread_id)
        self.assertGreaterEqual(len(first_instance_checkpoints), 1)
        latest = store.latest_checkpoint(thread_id=thread_id)
        self.assertIsNotNone(latest)

        restarted_store = LangGraphCheckpointStore(self.db_path)
        self.addCleanup(restarted_store.close)
        restarted_checkpoints = restarted_store.list_thread_checkpoints(thread_id)
        self.assertTrue(any(item.checkpoint_id == latest.checkpoint_id for item in restarted_checkpoints))

        resumed_graph = self._build_interrupt_graph(restarted_store)
        resumed_result = resumed_graph.invoke(
            Command(resume={"decision": "approve"}),
            config=restarted_store.checkpoint_config(thread_id=thread_id, checkpoint_id=latest.checkpoint_id),
        )
        self.assertEqual(resumed_result["final_answer"], "decision:approve")

    def test_pending_hitl_request_can_be_queried_after_restart(self) -> None:
        store = LangGraphCheckpointStore(self.db_path)
        self.addCleanup(store.close)
        request, created = store.record_pending_hitl(
            PendingHitlRequest(
                request_id="",
                run_id="run-1",
                thread_id="session-1",
                session_id="session-1",
                checkpoint_id="cp-1",
                capability_id="python_repl",
                capability_type="tool",
                display_name="Python REPL",
                risk_level="high",
                reason="approval required",
                proposed_input={"code": "print(2 + 2)"},
                requested_at="2026-04-08T09:00:00Z",
            )
        )
        self.assertTrue(created)
        restarted_store = LangGraphCheckpointStore(self.db_path)
        self.addCleanup(restarted_store.close)
        pending = restarted_store.pending_hitl(thread_id="session-1")
        self.assertIsNotNone(pending)
        self.assertEqual(pending.request_id, request.request_id)
        self.assertEqual(pending.checkpoint_id, "cp-1")


if __name__ == "__main__":
    unittest.main()
