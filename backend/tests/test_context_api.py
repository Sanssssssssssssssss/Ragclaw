from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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
from src.backend.context.models import ContextAssemblyDecision, ContextEnvelope, ContextModelCallSnapshot, ContextTurnSnapshot
from src.backend.context.store import context_store
from src.backend.runtime.session_manager import SessionManager


class ContextApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)
        self.session_manager = SessionManager(self.base_dir)
        self.session_manager.save_message("session-ctx", "user", "缁撳悎鐭ヨ瘑搴撲粙缁嶄竴涓嬩笁涓€閲嶅伐", message_id="msg-user", run_id="run-ctx")
        self.session_manager.save_message("session-ctx", "assistant", "Grounded summary", message_id="msg-assistant", turn_id="run-ctx:0", run_id="run-ctx")
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
        context_store.record_context_model_call(
            ContextModelCallSnapshot(
                call_id="run-ctx:0:knowledge_synthesis",
                session_id="session-ctx",
                run_id="run-ctx",
                thread_id="thread-ctx",
                turn_id="run-ctx:0",
                call_type="knowledge_synthesis_call",
                call_site="knowledge_synthesis",
                path_type="knowledge_qa",
                user_query="缁撳悎鐭ヨ瘑搴撲粙缁嶄竴涓嬩笁涓€閲嶅伐",
                context_envelope=ContextEnvelope(
                    system_block="[Context policy]\nPrefer retrieval evidence first.",
                    history_block="[Recent history]\nuser: 浣犲ソ",
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

    def test_context_turn_endpoints_return_list_detail_and_call(self) -> None:
        with (
            patch.object(context_api.agent_manager, "base_dir", self.base_dir),
            patch.object(context_api, "_thread_id_for", return_value="thread-ctx"),
        ):
            client = self._client()
            listing = client.get("/api/context/sessions/session-ctx/turns")
            detail = client.get("/api/context/sessions/session-ctx/turns/run-ctx%3A0")
            call = client.get("/api/context/sessions/session-ctx/turns/run-ctx%3A0/calls/run-ctx%3A0%3Aknowledge_synthesis")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(call.status_code, 200)
        self.assertEqual(listing.json()["items"][0]["turn_id"], "run-ctx:0")
        self.assertEqual(detail.json()["turn"]["context_envelope"]["evidence_block"], "[Retrieval evidence]\n1. knowledge/report.pdf|page 1")
        self.assertEqual(detail.json()["calls"][0]["call_id"], "run-ctx:0:knowledge_synthesis")
        self.assertEqual(call.json()["call"]["call_type"], "knowledge_synthesis_call")

    def test_context_quarantine_endpoints_expose_derived_memories_and_exclusion(self) -> None:
        memory = context_store.insert_memory(
            kind="semantic",
            namespace="project:test",
            title="Derived fact",
            content="Grounded summary",
            summary="Grounded summary",
            source="assistant_message",
            created_at="2026-04-09T11:00:00Z",
            fingerprint="fp-context-api",
            memory_type="project_fact",
            scope="project",
            source_turn_ids=("run-ctx:0",),
            source_run_ids=("run-ctx",),
            generated_by="context_writer",
            generated_at="2026-04-09T11:00:00Z",
        )
        with (
            patch.object(context_api.agent_manager, "base_dir", self.base_dir),
            patch.object(context_api.agent_manager, "session_manager", self.session_manager),
            patch.object(context_api.agent_manager, "get_harness_runtime", return_value=SimpleNamespace(now=lambda: "2026-04-09T11:10:00Z")),
            patch.object(context_api, "_thread_id_for", return_value="thread-ctx"),
        ):
            client = self._client()
            derived = client.get("/api/context/sessions/session-ctx/turns/run-ctx%3A0/derived-memories")
            excluded = client.post("/api/context/sessions/session-ctx/turns/run-ctx%3A0/exclude")

        self.assertEqual(derived.status_code, 200)
        self.assertEqual(excluded.status_code, 200)
        self.assertEqual(derived.json()["memories"][0]["memory_id"], memory.memory_id)
        self.assertTrue(excluded.json()["result"]["turn"]["excluded_from_context"])


if __name__ == "__main__":
    unittest.main()
