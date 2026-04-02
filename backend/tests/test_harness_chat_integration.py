from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import chat as chat_api


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
    ) -> dict[str, object]:
        payload = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "retrieval_steps": retrieval_steps,
            "usage": usage,
        }
        self.saved_messages.append(payload)
        return payload

    def set_title(self, _session_id: str, _title: str) -> None:
        return None


class FakeAgentManager:
    def __init__(self, base_dir: Path, events: list[dict[str, object]], error: Exception | None = None) -> None:
        self.base_dir = base_dir
        self.session_manager = FakeSessionManager()
        self._events = list(events)
        self._error = error

    async def astream(self, _message: str, _history: list[dict[str, object]]):
        for event in self._events:
            yield dict(event)
        if self._error is not None:
            raise self._error

    async def generate_title(self, _message: str) -> str:
        return "ignored"


class HarnessChatIntegrationTests(unittest.TestCase):
    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(chat_api.router, prefix="/api")
        return app

    def _read_single_trace(self, runs_dir: Path) -> tuple[dict[str, object], list[str]]:
        traces = list(runs_dir.glob("*.jsonl"))
        self.assertEqual(len(traces), 1)
        records = [json.loads(line) for line in traces[0].read_text(encoding="utf-8").splitlines() if line.strip()]
        names = [record["payload"]["name"] for record in records if record.get("record_type") == "event"]
        outcome_records = [record for record in records if record.get("record_type") == "run_outcome"]
        self.assertEqual(len(outcome_records), 1)
        return outcome_records[0]["payload"], names

    def test_chat_stream_preserves_legacy_sse_and_writes_completed_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_manager = FakeAgentManager(
                root,
                events=[
                    {
                        "type": "_harness_route",
                        "decision": {
                            "intent": "knowledge_qa",
                            "needs_tools": False,
                            "needs_retrieval": True,
                            "allowed_tools": [],
                            "confidence": 0.9,
                            "reason_short": "unit route",
                            "source": "unit",
                            "ambiguity_flags": [],
                            "escalated": False,
                            "model_name": "",
                            "subtype": "",
                        },
                    },
                    {
                        "type": "_harness_skill",
                        "decision": {
                            "use_skill": False,
                            "skill_name": "",
                            "confidence": 0.1,
                            "reason_short": "not needed",
                        },
                    },
                    {
                        "type": "retrieval",
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
                                "score": 0.8,
                                "parent_id": None,
                            }
                        ],
                    },
                    {"type": "token", "content": "你好"},
                    {"type": "done", "content": "你好", "usage": {"input_tokens": 3, "output_tokens": 1}},
                ],
            )
            app = self._build_app()
            with patch.object(chat_api, "agent_manager", fake_manager):
                client = TestClient(app)
                response = client.post(
                    "/api/chat",
                    json={"message": "test", "session_id": "session-1", "stream": True},
                )

            body = response.text
            self.assertIn("event: retrieval", body)
            self.assertIn("event: token", body)
            self.assertIn("event: done", body)
            self.assertNotIn("_harness_route", body)
            self.assertNotIn("_harness_skill", body)
            self.assertEqual(len(fake_manager.session_manager.saved_messages), 2)

            outcome, event_names = self._read_single_trace(root / "storage" / "runs")
            self.assertEqual(outcome["status"], "completed")
            self.assertIn("run.started", event_names)
            self.assertIn("route.decided", event_names)
            self.assertIn("skill.decided", event_names)
            self.assertIn("retrieval.completed", event_names)
            self.assertIn("answer.completed", event_names)
            self.assertIn("run.completed", event_names)

    def test_chat_stream_writes_failed_trace_on_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_manager = FakeAgentManager(
                root,
                events=[
                    {
                        "type": "_harness_route",
                        "decision": {
                            "intent": "direct_answer",
                            "needs_tools": False,
                            "needs_retrieval": False,
                            "allowed_tools": [],
                            "confidence": 0.9,
                            "reason_short": "unit route",
                            "source": "unit",
                            "ambiguity_flags": [],
                            "escalated": False,
                            "model_name": "",
                            "subtype": "",
                        },
                    },
                    {"type": "token", "content": "partial"},
                ],
                error=RuntimeError("boom"),
            )
            app = self._build_app()
            with patch.object(chat_api, "agent_manager", fake_manager):
                client = TestClient(app)
                response = client.post(
                    "/api/chat",
                    json={"message": "test", "session_id": "session-1", "stream": True},
                )

            body = response.text
            self.assertIn("event: error", body)

            outcome, event_names = self._read_single_trace(root / "storage" / "runs")
            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(outcome["error_message"], "boom")
            self.assertIn("run.started", event_names)
            self.assertIn("answer.delta", event_names)
            self.assertIn("run.failed", event_names)


if __name__ == "__main__":
    unittest.main()
