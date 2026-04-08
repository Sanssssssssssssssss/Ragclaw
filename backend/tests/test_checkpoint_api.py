from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.api import sessions as sessions_api
from src.backend.observability.types import HarnessEvent
from src.backend.orchestration.checkpointing import CheckpointSummary, PendingHitlRequest


class FakeSessionManager:
    def __init__(self) -> None:
        self.saved_messages: list[dict[str, object]] = []

    def list_sessions(self) -> list[dict[str, object]]:
        return []

    def create_session(self, title: str = "New Session") -> dict[str, object]:
        return {"id": "session-1", "title": title}

    def rename_session(self, session_id: str, title: str) -> dict[str, object]:
        return {"id": session_id, "title": title}

    def delete_session(self, _session_id: str) -> None:
        return None

    def load_session(self, _session_id: str) -> list[dict[str, object]]:
        return []

    def load_session_for_agent(self, _session_id: str) -> list[dict[str, str]]:
        return [{"role": "user", "content": "Resume this direct-answer run from a checkpoint and finish it."}]

    def get_history(self, session_id: str) -> dict[str, object]:
        return {"id": session_id, "messages": []}

    def set_title(self, _session_id: str, _title: str) -> None:
        return None

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls=None,
        retrieval_steps=None,
        usage=None,
        run_meta=None,
        checkpoint_events=None,
        hitl_events=None,
    ) -> dict[str, object]:
        payload = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "retrieval_steps": retrieval_steps,
            "usage": usage,
            "run_meta": run_meta,
            "checkpoint_events": checkpoint_events,
            "hitl_events": hitl_events,
        }
        self.saved_messages.append(payload)
        return payload


class FakeAgentManager:
    def __init__(self) -> None:
        self.session_manager = FakeSessionManager()
        self.base_dir = BACKEND_DIR

    async def generate_title(self, message: str) -> str:
        return message[:10] or "New Session"


class FakeRuntime:
    def __init__(self, events: list[HarnessEvent]) -> None:
        self._events = list(events)

    async def run_with_executor(self, **_kwargs):
        for event in self._events:
            yield event


def _event(name: str, payload: dict, event_id: str) -> HarnessEvent:
    return HarnessEvent(
        event_id=event_id,
        run_id="run-1",
        name=name,  # type: ignore[arg-type]
        ts="2026-04-07T12:00:00Z",
        payload=payload,
    )


class CheckpointApiTests(unittest.TestCase):
    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(sessions_api.router, prefix="/api")
        return app

    def test_list_checkpoints_returns_thread_scoped_payload(self) -> None:
        app = self._build_app()
        fake_manager = FakeAgentManager()
        checkpoint = CheckpointSummary(
            checkpoint_id="cp-1",
            thread_id="session-1",
            checkpoint_ns="",
            created_at="2026-04-07T12:00:00Z",
            source="loop",
            step=2,
            run_id="run-1",
            session_id="session-1",
            user_message="hello",
            route_intent="direct_answer",
            final_answer="done",
            is_latest=False,
            state_label="interrupted",
            resume_eligible=True,
        )
        with (
            patch.object(sessions_api, "agent_manager", fake_manager),
            patch.object(sessions_api.checkpoint_store, "list_thread_checkpoints", return_value=[checkpoint]),
        ):
            client = TestClient(app)
            response = client.get("/api/sessions/session-1/checkpoints")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["thread_id"], "session-1")
        self.assertEqual(payload["checkpoints"][0]["checkpoint_id"], "cp-1")

    def test_resume_checkpoint_streams_status_and_persists_assistant_only(self) -> None:
        app = self._build_app()
        fake_manager = FakeAgentManager()
        checkpoint = CheckpointSummary(
            checkpoint_id="cp-1",
            thread_id="session-1",
            checkpoint_ns="",
            created_at="2026-04-07T12:00:00Z",
            source="loop",
            step=2,
            run_id="run-1",
            session_id="session-1",
            user_message="resume original question",
            route_intent="direct_answer",
            final_answer="",
            is_latest=False,
            state_label="interrupted",
            resume_eligible=True,
        )
        runtime = FakeRuntime(
            [
                _event(
                    "run.started",
                    {
                        "session_id": "session-1",
                        "thread_id": "session-1",
                        "checkpoint_id": "cp-1",
                        "resume_source": "checkpoint_api",
                        "run_status": "resumed",
                        "orchestration_engine": "langgraph",
                    },
                    "evt-1",
                ),
                _event(
                    "checkpoint.interrupted",
                    {
                        "thread_id": "session-1",
                        "checkpoint_id": "cp-1",
                        "resume_source": "checkpoint_api",
                        "orchestration_engine": "langgraph",
                    },
                    "evt-2",
                ),
                _event(
                    "checkpoint.resumed",
                    {
                        "thread_id": "session-1",
                        "checkpoint_id": "cp-1",
                        "resume_source": "checkpoint_api",
                        "orchestration_engine": "langgraph",
                    },
                    "evt-3",
                ),
                _event("answer.started", {"segment_index": 0, "content": "", "final": False}, "evt-4"),
                _event("answer.delta", {"segment_index": 0, "content": "resumed answer", "final": False}, "evt-5"),
                _event("answer.completed", {"segment_index": 0, "content": "resumed answer", "final": True}, "evt-6"),
                _event(
                    "checkpoint.created",
                    {
                        "thread_id": "session-1",
                        "checkpoint_id": "cp-2",
                        "orchestration_engine": "langgraph",
                        "state_label": "fresh",
                    },
                    "evt-7",
                ),
                _event("run.completed", {"route_intent": "direct_answer"}, "evt-8"),
            ]
        )

        with (
            patch.object(sessions_api, "agent_manager", fake_manager),
            patch.object(sessions_api.checkpoint_store, "get_checkpoint", return_value=checkpoint),
            patch.object(sessions_api, "_build_runtime_and_resume_executor", return_value=(runtime, object())),
        ):
            client = TestClient(app)
            response = client.post("/api/sessions/session-1/checkpoints/cp-1/resume", json={"stream": True})

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: run_status", response.text)
        self.assertIn("event: checkpoint_resumed", response.text)
        self.assertIn("event: done", response.text)
        self.assertEqual(len(fake_manager.session_manager.saved_messages), 1)
        self.assertEqual(fake_manager.session_manager.saved_messages[0]["role"], "assistant")
        self.assertEqual(fake_manager.session_manager.saved_messages[0]["run_meta"]["status"], "resumed")

    def test_get_pending_hitl_returns_interrupt_payload(self) -> None:
        app = self._build_app()
        fake_manager = FakeAgentManager()
        pending = PendingHitlRequest(
            request_id="req-1",
            run_id="run-1",
            thread_id="session-1",
            session_id="session-1",
            checkpoint_id="cp-hitl",
            capability_id="python_repl",
            capability_type="tool",
            display_name="Python REPL",
            risk_level="high",
            reason="Python REPL requires explicit approval before execution.",
            proposed_input={"message": "calculate 2 + 2"},
            requested_at="2026-04-07T12:00:00Z",
        )
        with (
            patch.object(sessions_api, "agent_manager", fake_manager),
            patch.object(sessions_api.checkpoint_store, "pending_hitl", return_value=pending),
        ):
            client = TestClient(app)
            response = client.get("/api/sessions/session-1/hitl")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session_id"], "session-1")
        self.assertEqual(payload["pending_interrupt"]["capability_id"], "python_repl")
        self.assertEqual(payload["pending_interrupt"]["checkpoint_id"], "cp-hitl")
        self.assertEqual(payload["pending_interrupt"]["request_id"], "req-1")

    def test_get_hitl_audit_returns_request_and_decision(self) -> None:
        app = self._build_app()
        fake_manager = FakeAgentManager()
        pending = PendingHitlRequest(
            request_id="req-1",
            run_id="run-1",
            thread_id="session-1",
            session_id="session-1",
            checkpoint_id="cp-hitl",
            capability_id="python_repl",
            capability_type="tool",
            display_name="Python REPL",
            risk_level="high",
            reason="Python REPL requires explicit approval before execution.",
            proposed_input={"message": "calculate 2 + 2"},
            requested_at="2026-04-07T12:00:00Z",
            status="approve",
        )
        decision = {
            "decision_id": "decision-1",
            "request_id": "req-1",
            "decision": "approve",
            "actor_id": "session:session-1",
            "actor_type": "session_user",
            "decided_at": "2026-04-07T12:01:00Z",
            "resume_source": "hitl_api",
            "approved_input_snapshot": {"message": "calculate 2 + 2"},
            "rejected_input_snapshot": None,
        }
        with (
            patch.object(sessions_api, "agent_manager", fake_manager),
            patch.object(sessions_api.checkpoint_store, "get_hitl_request", return_value=pending),
            patch.object(sessions_api.checkpoint_store, "get_hitl_decision") as get_decision_mock,
        ):
            get_decision_mock.return_value = type("DecisionObj", (), {"to_dict": lambda self: decision})()
            client = TestClient(app)
            response = client.get("/api/sessions/session-1/hitl/cp-hitl")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["request"]["request_id"], "req-1")
        self.assertEqual(payload["decision"]["decision_id"], "decision-1")

    def test_submit_hitl_decision_streams_resume_and_persists_assistant_only(self) -> None:
        app = self._build_app()
        fake_manager = FakeAgentManager()
        runtime = FakeRuntime(
            [
                _event(
                    "run.started",
                    {
                        "session_id": "session-1",
                        "thread_id": "session-1",
                        "checkpoint_id": "cp-hitl",
                        "resume_source": "hitl_api",
                        "run_status": "restoring",
                        "orchestration_engine": "langgraph",
                    },
                    "evt-1",
                ),
                _event(
                    "checkpoint.resumed",
                    {
                        "thread_id": "session-1",
                        "checkpoint_id": "cp-hitl",
                        "resume_source": "hitl_api",
                        "orchestration_engine": "langgraph",
                    },
                    "evt-2",
                ),
                _event(
                    "hitl.approved",
                    {
                        "run_id": "run-1",
                        "thread_id": "session-1",
                        "session_id": "session-1",
                        "capability_id": "python_repl",
                        "capability_type": "tool",
                        "display_name": "Python REPL",
                        "risk_level": "high",
                        "reason": "Python REPL requires explicit approval before execution.",
                        "proposed_input": {"message": "calculate 2 + 2"},
                        "request_id": "req-1",
                        "decision_id": "decision-1",
                        "actor_id": "session:session-1",
                        "actor_type": "session_user",
                        "decided_at": "2026-04-07T12:01:00Z",
                        "requested_at": "2026-04-07T12:00:00Z",
                        "approved_input_snapshot": {"message": "calculate 2 + 2"},
                        "checkpoint_id": "cp-hitl",
                        "resume_source": "hitl_api",
                        "orchestration_engine": "langgraph",
                    },
                    "evt-3",
                ),
                _event(
                    "tool.started",
                    {"tool": "python_repl", "input": "{\"code\":\"print(2 + 2)\"}", "call_id": "call-1"},
                    "evt-4",
                ),
                _event(
                    "tool.completed",
                    {"tool": "python_repl", "input": "{\"code\":\"print(2 + 2)\"}", "output": "4", "call_id": "call-1"},
                    "evt-5",
                ),
                _event("answer.started", {"segment_index": 0, "content": "", "final": False}, "evt-6"),
                _event("answer.delta", {"segment_index": 0, "content": "The result is 4.", "final": False}, "evt-7"),
                _event("answer.completed", {"segment_index": 0, "content": "The result is 4.", "final": True}, "evt-8"),
                _event(
                    "checkpoint.created",
                    {
                        "thread_id": "session-1",
                        "checkpoint_id": "cp-hitl-2",
                        "orchestration_engine": "langgraph",
                        "state_label": "fresh",
                    },
                    "evt-9",
                ),
                _event("run.completed", {"route_intent": "workspace_file_ops"}, "evt-10"),
            ]
        )
        pending = PendingHitlRequest(
            request_id="req-1",
            run_id="run-1",
            thread_id="session-1",
            session_id="session-1",
            checkpoint_id="cp-hitl",
            capability_id="python_repl",
            capability_type="tool",
            display_name="Python REPL",
            risk_level="high",
            reason="Python REPL requires explicit approval before execution.",
            proposed_input={"message": "calculate 2 + 2"},
            requested_at="2026-04-07T12:00:00Z",
        )
        decision_record = type(
            "DecisionObj",
            (),
            {
                "decision_id": "decision-1",
                "request_id": "req-1",
                "decision": "approve",
                "actor_id": "session:session-1",
                "actor_type": "session_user",
                "decided_at": "2026-04-07T12:01:00Z",
                "resume_source": "hitl_api",
                "to_dict": lambda self: {
                    "decision_id": "decision-1",
                    "request_id": "req-1",
                    "decision": "approve",
                    "actor_id": "session:session-1",
                    "actor_type": "session_user",
                    "decided_at": "2026-04-07T12:01:00Z",
                    "resume_source": "hitl_api",
                    "approved_input_snapshot": {"message": "calculate 2 + 2"},
                    "rejected_input_snapshot": None,
                },
            },
        )()

        with (
            patch.object(sessions_api, "agent_manager", fake_manager),
            patch.object(
                sessions_api.checkpoint_store,
                "record_hitl_decision",
                return_value=(pending, decision_record, True),
            ),
            patch.object(sessions_api, "_build_runtime_and_resume_executor", return_value=(runtime, object())),
        ):
            client = TestClient(app)
            response = client.post(
                "/api/sessions/session-1/hitl/cp-hitl/decision",
                json={"decision": "approve", "stream": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: hitl_approved", response.text)
        self.assertIn("event: done", response.text)
        self.assertEqual(len(fake_manager.session_manager.saved_messages), 1)
        self.assertEqual(fake_manager.session_manager.saved_messages[0]["role"], "assistant")
        self.assertEqual(fake_manager.session_manager.saved_messages[0]["run_meta"]["status"], "resumed")
        self.assertEqual(fake_manager.session_manager.saved_messages[0]["hitl_events"][0]["type"], "approved")

    def test_duplicate_hitl_decision_is_safely_rejected(self) -> None:
        app = self._build_app()
        fake_manager = FakeAgentManager()
        pending = PendingHitlRequest(
            request_id="req-1",
            run_id="run-1",
            thread_id="session-1",
            session_id="session-1",
            checkpoint_id="cp-hitl",
            capability_id="python_repl",
            capability_type="tool",
            display_name="Python REPL",
            risk_level="high",
            reason="Python REPL requires explicit approval before execution.",
            proposed_input={"message": "calculate 2 + 2"},
            requested_at="2026-04-07T12:00:00Z",
            status="approve",
        )
        decision_record = type(
            "DecisionObj",
            (),
            {
                "to_dict": lambda self: {
                    "decision_id": "decision-1",
                    "request_id": "req-1",
                    "decision": "approve",
                    "actor_id": "session:session-1",
                    "actor_type": "session_user",
                    "decided_at": "2026-04-07T12:01:00Z",
                    "resume_source": "hitl_api",
                    "approved_input_snapshot": {"message": "calculate 2 + 2"},
                    "rejected_input_snapshot": None,
                }
            },
        )()
        with (
            patch.object(sessions_api, "agent_manager", fake_manager),
            patch.object(
                sessions_api.checkpoint_store,
                "record_hitl_decision",
                return_value=(pending, decision_record, False),
            ) as record_mock,
            patch.object(sessions_api, "_build_runtime_and_resume_executor") as build_runtime_mock,
        ):
            client = TestClient(app)
            response = client.post(
                "/api/sessions/session-1/hitl/cp-hitl/decision",
                json={"decision": "approve", "stream": False},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("decision", response.json()["detail"])
        record_mock.assert_called_once()
        build_runtime_mock.assert_not_called()

    def test_duplicate_hitl_reject_is_safely_rejected(self) -> None:
        app = self._build_app()
        fake_manager = FakeAgentManager()
        pending = PendingHitlRequest(
            request_id="req-1",
            run_id="run-1",
            thread_id="session-1",
            session_id="session-1",
            checkpoint_id="cp-hitl",
            capability_id="python_repl",
            capability_type="tool",
            display_name="Python REPL",
            risk_level="high",
            reason="Python REPL requires explicit approval before execution.",
            proposed_input={"message": "calculate 2 + 2"},
            requested_at="2026-04-07T12:00:00Z",
            status="reject",
        )
        decision_record = type(
            "DecisionObj",
            (),
            {
                "to_dict": lambda self: {
                    "decision_id": "decision-2",
                    "request_id": "req-1",
                    "decision": "reject",
                    "actor_id": "session:session-1",
                    "actor_type": "session_user",
                    "decided_at": "2026-04-07T12:01:00Z",
                    "resume_source": "hitl_api",
                    "approved_input_snapshot": None,
                    "rejected_input_snapshot": {"message": "calculate 2 + 2"},
                }
            },
        )()
        with (
            patch.object(sessions_api, "agent_manager", fake_manager),
            patch.object(
                sessions_api.checkpoint_store,
                "record_hitl_decision",
                return_value=(pending, decision_record, False),
            ) as record_mock,
            patch.object(sessions_api, "_build_runtime_and_resume_executor") as build_runtime_mock,
        ):
            client = TestClient(app)
            response = client.post(
                "/api/sessions/session-1/hitl/cp-hitl/decision",
                json={"decision": "reject", "stream": False},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["decision"]["decision"], "reject")
        record_mock.assert_called_once()
        build_runtime_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
