from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.backend.api.adapters import LegacyChatAccumulator
from src.backend.observability.types import HarnessEvent


def _event(name: str, payload: dict, event_id: str) -> HarnessEvent:
    return HarnessEvent(
        event_id=event_id,
        run_id="run-1",
        name=name,  # type: ignore[arg-type]
        ts="2026-04-03T12:00:00Z",
        payload=payload,
    )


class HarnessAdapterTests(unittest.TestCase):
    def test_route_and_skill_events_do_not_leak_to_legacy_sse(self) -> None:
        accumulator = LegacyChatAccumulator()
        events = accumulator.consume(_event("route.decided", {"intent": "knowledge_qa"}, "evt-1"))
        events += accumulator.consume(_event("skill.decided", {"use_skill": False, "skill_name": ""}, "evt-2"))
        self.assertEqual(events, [])

    def test_capability_events_do_not_leak_to_legacy_sse(self) -> None:
        accumulator = LegacyChatAccumulator()
        events = accumulator.consume(
            _event(
                "capability.completed",
                {"capability_id": "terminal", "capability_type": "tool", "call_id": "cap-1", "status": "success"},
                "evt-3",
            )
        )
        self.assertEqual(events, [])

    def test_answer_delta_and_completed_stay_coherent(self) -> None:
        accumulator = LegacyChatAccumulator()
        outputs = []
        outputs += accumulator.consume(_event("answer.started", {"segment_index": 0, "content": "", "final": False}, "evt-1"))
        outputs += accumulator.consume(_event("answer.delta", {"segment_index": 0, "content": "hello ", "final": False}, "evt-2"))
        outputs += accumulator.consume(
            _event(
                "answer.completed",
                {"segment_index": 0, "content": "hello world", "final": True, "input_tokens": 3, "output_tokens": 2},
                "evt-3",
            )
        )
        self.assertIn(("token", {"content": "hello "}), outputs)
        done_payload = next(payload for name, payload in outputs if name == "done")
        self.assertEqual(done_payload["content"], "hello world")
        self.assertEqual(done_payload["usage"], {"input_tokens": 3, "output_tokens": 2})
        self.assertEqual(done_payload["run_meta"], {})
        self.assertEqual(done_payload["checkpoint_events"], [])
        self.assertEqual(done_payload["hitl_events"], [])
        self.assertEqual(accumulator.current_segment["content"], "hello world")

    def test_segment_index_jump_emits_new_response_once(self) -> None:
        accumulator = LegacyChatAccumulator()
        outputs = []
        outputs += accumulator.consume(_event("answer.started", {"segment_index": 0, "content": "", "final": False}, "evt-1"))
        outputs += accumulator.consume(_event("answer.delta", {"segment_index": 0, "content": "first segment", "final": False}, "evt-2"))
        outputs += accumulator.consume(_event("answer.started", {"segment_index": 1, "content": "", "final": False}, "evt-3"))
        outputs += accumulator.consume(_event("answer.delta", {"segment_index": 1, "content": "second segment", "final": False}, "evt-4"))
        self.assertEqual([item[0] for item in outputs].count("new_response"), 1)
        self.assertEqual(accumulator.segments[0]["content"], "first segment")

    def test_tool_completion_matches_by_call_id(self) -> None:
        accumulator = LegacyChatAccumulator()
        accumulator.consume(_event("tool.started", {"tool": "terminal", "input": "dir", "call_id": "call-1"}, "evt-1"))
        accumulator.consume(_event("tool.completed", {"tool": "terminal", "input": "dir", "output": "a.txt", "call_id": "call-1"}, "evt-2"))
        self.assertEqual(accumulator.current_segment["tool_calls"][0]["output"], "a.txt")

    def test_checkpoint_events_are_mapped_to_legacy_status_and_markers(self) -> None:
        accumulator = LegacyChatAccumulator()
        outputs = []
        outputs += accumulator.consume(
            _event(
                "run.started",
                {
                    "run_status": "fresh",
                    "thread_id": "session-1",
                    "checkpoint_id": "",
                    "resume_source": "",
                    "orchestration_engine": "langgraph",
                },
                "evt-1",
            )
        )
        outputs += accumulator.consume(
            _event(
                "checkpoint.interrupted",
                {
                    "thread_id": "session-1",
                    "checkpoint_id": "cp-1",
                    "resume_source": "checkpoint_api",
                    "orchestration_engine": "langgraph",
                },
                "evt-2",
            )
        )
        outputs += accumulator.consume(
            _event(
                "checkpoint.resumed",
                {
                    "thread_id": "session-1",
                    "checkpoint_id": "cp-1",
                    "resume_source": "checkpoint_api",
                    "orchestration_engine": "langgraph",
                },
                "evt-3",
            )
        )
        output_names = [name for name, _payload in outputs]
        self.assertIn("run_status", output_names)
        self.assertIn("checkpoint_interrupted", output_names)
        self.assertIn("checkpoint_resumed", output_names)
        self.assertEqual(accumulator.run_meta["status"], "resumed")
        self.assertEqual(accumulator.checkpoint_events[-1]["checkpoint_id"], "cp-1")

    def test_hitl_events_are_mapped_to_legacy_markers(self) -> None:
        accumulator = LegacyChatAccumulator()
        outputs = accumulator.consume(
            _event(
                "hitl.requested",
                {
                    "run_id": "run-1",
                    "thread_id": "session-1",
                    "session_id": "session-1",
                    "capability_id": "python_repl",
                    "capability_type": "tool",
                    "display_name": "Python REPL",
                    "risk_level": "high",
                    "reason": "requires approval",
                    "proposed_input": {"message": "calculate 2+2"},
                    "checkpoint_id": "cp-hitl",
                    "resume_source": "hitl_api",
                    "orchestration_engine": "langgraph",
                },
                "evt-hitl",
            )
        )
        self.assertIn("run_status", [name for name, _payload in outputs])
        self.assertIn("hitl_requested", [name for name, _payload in outputs])
        self.assertEqual(accumulator.run_meta["status"], "interrupted")
        self.assertEqual(accumulator.hitl_events[-1]["capability_id"], "python_repl")


if __name__ == "__main__":
    unittest.main()
