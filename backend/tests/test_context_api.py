from __future__ import annotations

import sys
import tempfile
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

from src.backend.api import context as context_api
from src.backend.context.models import ContextAssemblyDecision, ContextEnvelope, ContextTurnSnapshot
from src.backend.context.store import context_store


class ContextApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)
        context_store.record_context_turn_snapshot(
            ContextTurnSnapshot(
                turn_id="run-ctx:0",
                session_id="session-ctx",
                run_id="run-ctx",
                thread_id="thread-ctx",
                assistant_message_id=None,
                segment_index=0,
                call_site="knowledge_synthesis",
                path_type="knowledge_qa",
                user_query="结合知识库介绍一下三一重工",
                context_envelope=ContextEnvelope(
                    system_block="[Context policy]\nPrefer retrieval evidence first.",
                    history_block="[Recent history]\nuser: 你好",
                    working_memory_block="[Working memory]\ncurrent_goal: grounded answer",
                    episodic_block="",
                    semantic_block="",
                    procedural_block="",
                    conversation_block="",
                    artifact_block="",
                    evidence_block="[Retrieval evidence]\n1. knowledge/report.pdf|page 1",
                    budget_report={"retrieval_evidence": 64},
                ),
                assembly_decision=ContextAssemblyDecision(
                    path_type="knowledge_qa",
                    selected_history_ids=("history:0",),
                    selected_memory_ids=("working:thread-ctx",),
                    selected_artifact_ids=(),
                    selected_evidence_ids=("knowledge/report.pdf|page 1",),
                    selected_conversation_ids=(),
                    dropped_items=(),
                    truncation_reason="",
                ),
                budget_report={
                    "allocated": {"retrieval_evidence": 200},
                    "used": {"retrieval_evidence": 64},
                    "excluded_from_prompt": ["raw checkpoint blob"],
                },
                selected_memory_ids=("working:thread-ctx",),
                selected_artifact_ids=(),
                selected_evidence_ids=("knowledge/report.pdf|page 1",),
                selected_conversation_ids=(),
                dropped_items=(),
                truncation_reason="",
                run_status="fresh",
                resume_source="",
                checkpoint_id="",
                orchestration_engine="langgraph",
                model_invoked=True,
                created_at="2026-04-09T11:00:00Z",
            )
        )

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(context_api.router, prefix="/api")
        return TestClient(app)

    def test_context_turn_endpoints_return_list_and_detail(self) -> None:
        with (
            patch.object(context_api.agent_manager, "base_dir", self.base_dir),
            patch.object(context_api, "_thread_id_for", return_value="thread-ctx"),
        ):
            client = self._client()
            listing = client.get("/api/context/sessions/session-ctx/turns")
            detail = client.get("/api/context/sessions/session-ctx/turns/run-ctx%3A0")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(listing.json()["items"][0]["turn_id"], "run-ctx:0")
        self.assertEqual(detail.json()["turn"]["context_envelope"]["evidence_block"], "[Retrieval evidence]\n1. knowledge/report.pdf|page 1")


if __name__ == "__main__":
    unittest.main()
