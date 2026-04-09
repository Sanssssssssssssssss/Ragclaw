from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
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

from src.backend.api import chat as chat_api
from src.backend.observability.types import HarnessEvent


class FakeSessionManager:
    def __init__(self) -> None:
        self.saved_messages: list[dict[str, object]] = []

    def load_session_record(self, session_id: str) -> dict[str, object]:
        return {"id": session_id, "title": "existing title", "messages": []}

    def load_session_for_agent(self, _session_id: str) -> list[dict[str, str]]:
        return []

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
        message_id=None,
        turn_id=None,
        run_id=None,
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
            "message_id": message_id,
            "turn_id": turn_id,
            "run_id": run_id,
        }
        self.saved_messages.append(payload)
        return payload

    def set_title(self, _session_id: str, _title: str) -> None:
        return None


class FakeAgentManager:
    def __init__(self) -> None:
        self.session_manager = FakeSessionManager()

    async def generate_title(self, _message: str) -> str:
        return "ignored"


@dataclass
class FakeHandle:
    run_id: str = "run-1"


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
        ts="2026-04-03T12:00:00Z",
        payload=payload,
    )


class HarnessChatIntegrationTests(unittest.TestCase):
    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(chat_api.router, prefix="/api")
        return app

    def test_chat_stream_consumes_canonical_harness_events(self) -> None:
        fake_manager = FakeAgentManager()
        runtime = FakeRuntime(
            [
                _event("run.started", {"session_id": "session-1"}, "evt-1"),
                _event(
                    "capability.completed",
                    {"capability_id": "terminal", "capability_type": "tool", "call_id": "cap-1", "status": "success"},
                    "evt-cap",
                ),
                _event(
                    "retrieval.completed",
                    {
                        "kind": "knowledge",
                        "stage": "fused",
                        "title": "test retrieval",
                        "message": "",
                        "results": [
                            {
                                "source_path": "knowledge/report.pdf",
                                "source_type": "pdf",
                                "locator": "page 1",
                                "snippet": "evidence",
                                "channel": "fused",
                            }
                        ],
                    },
                    "evt-2",
                ),
                _event("answer.started", {"segment_index": 0, "content": "", "final": False}, "evt-3"),
                _event("answer.delta", {"segment_index": 0, "content": "hello", "final": False}, "evt-4"),
                _event(
                    "answer.completed",
                    {"segment_index": 0, "content": "hello", "final": True, "input_tokens": 3, "output_tokens": 1},
                    "evt-5",
                ),
                _event("run.completed", {"route_intent": "knowledge_qa"}, "evt-6"),
            ]
        )

        app = self._build_app()
        with (
            patch.object(chat_api, "agent_manager", fake_manager),
            patch.object(chat_api, "_build_runtime_and_executor", return_value=(runtime, object())),
        ):
            client = TestClient(app)
            response = client.post("/api/chat", json={"message": "test", "session_id": "session-1", "stream": True})

        body = response.text
        self.assertIn("event: retrieval", body)
        self.assertIn("event: token", body)
        self.assertIn("event: done", body)
        self.assertNotIn("_harness_route", body)
        self.assertNotIn("event: capability", body)
        self.assertEqual(len(fake_manager.session_manager.saved_messages), 2)

    def test_chat_stream_persists_error_when_run_fails(self) -> None:
        fake_manager = FakeAgentManager()
        runtime = FakeRuntime(
            [
                _event("run.started", {"session_id": "session-1"}, "evt-1"),
                _event("answer.started", {"segment_index": 0, "content": "", "final": False}, "evt-2"),
                _event("answer.delta", {"segment_index": 0, "content": "partial", "final": False}, "evt-3"),
                _event("run.failed", {"error_message": "boom"}, "evt-4"),
            ]
        )

        app = self._build_app()
        with (
            patch.object(chat_api, "agent_manager", fake_manager),
            patch.object(chat_api, "_build_runtime_and_executor", return_value=(runtime, object())),
        ):
            client = TestClient(app)
            response = client.post("/api/chat", json={"message": "test", "session_id": "session-1", "stream": True})

        body = response.text
        self.assertIn("event: error", body)
        self.assertIn("boom", fake_manager.session_manager.saved_messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
