from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import TypedDict

from langgraph.graph import START, END, StateGraph

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.orchestration.checkpointing import LangGraphCheckpointStore


class _State(TypedDict, total=False):
    run_id: str
    session_id: str | None
    user_message: str
    final_answer: str


class LangGraphCheckpointingTests(unittest.TestCase):
    def test_in_memory_checkpointer_lists_resume_eligible_checkpoints(self) -> None:
        store = LangGraphCheckpointStore()
        graph = StateGraph(_State)
        graph.add_node("step_one", lambda state: {"run_id": state["run_id"], "session_id": state["session_id"], "user_message": state["user_message"]})
        graph.add_node("step_two", lambda state: {"final_answer": "done"})
        graph.add_edge(START, "step_one")
        graph.add_edge("step_one", "step_two")
        graph.add_edge("step_two", END)
        compiled = graph.compile(checkpointer=store.saver)

        thread_id = store.thread_id_for(session_id="session-1", run_id="run-1")
        compiled.invoke(
            {"run_id": "run-1", "session_id": "session-1", "user_message": "hello"},
            config={"configurable": {"thread_id": thread_id}},
        )

        checkpoints = store.list_thread_checkpoints(thread_id)
        self.assertGreaterEqual(len(checkpoints), 2)
        self.assertTrue(any(item.resume_eligible for item in checkpoints))
        latest = store.latest_checkpoint(thread_id=thread_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.thread_id, "session-1")


if __name__ == "__main__":
    unittest.main()
