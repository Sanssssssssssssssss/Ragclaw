from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.backend.observability.types import (
    CANONICAL_EVENT_NAMES,
    AnswerRecord,
    CapabilityCallRecord,
    GuardResult,
    HarnessEvent,
    RetrievalEvidenceRecord,
    RetrievalRecord,
    RouteDecisionRecord,
    RunMetadata,
    RunOutcome,
    SkillDecisionRecord,
    ToolCallRecord,
)


class HarnessTypesTests(unittest.TestCase):
    def test_canonical_event_names_are_unique(self) -> None:
        self.assertEqual(len(CANONICAL_EVENT_NAMES), len(set(CANONICAL_EVENT_NAMES)))

    def test_run_metadata_serializes(self) -> None:
        metadata = RunMetadata(
            run_id="run-123",
            session_id="session-1",
            user_message="hello",
            source="chat_api",
            started_at="2026-04-02T12:00:00Z",
        )
        self.assertEqual(
            metadata.to_dict(),
            {
                "run_id": "run-123",
                "session_id": "session-1",
                "user_message": "hello",
                "source": "chat_api",
                "started_at": "2026-04-02T12:00:00Z",
            },
        )

    def test_route_decision_direct_answer_cannot_expose_tools(self) -> None:
        with self.assertRaises(ValueError):
            RouteDecisionRecord(
                intent="direct_answer",
                needs_tools=False,
                needs_retrieval=False,
                allowed_tools=("terminal",),
            )

    def test_skill_decision_requires_skill_name_when_enabled(self) -> None:
        with self.assertRaises(ValueError):
            SkillDecisionRecord(use_skill=True, skill_name="")

    def test_retrieval_record_serializes_nested_results(self) -> None:
        record = RetrievalRecord(
            kind="knowledge",
            stage="vector",
            title="Vector retrieval",
            results=(
                RetrievalEvidenceRecord(
                    source_path="knowledge/report.pdf",
                    source_type="pdf",
                    locator="page 1",
                    snippet="evidence",
                    channel="vector",
                    score=0.9,
                ),
            ),
        )
        payload = record.to_dict()
        self.assertEqual(payload["stage"], "vector")
        self.assertEqual(payload["results"][0]["source_path"], "knowledge/report.pdf")

    def test_tool_and_answer_records_validate_basic_invariants(self) -> None:
        tool = ToolCallRecord(tool="terminal", input="dir", output="a.txt")
        answer = AnswerRecord(content="done", segment_index=0, final=True, input_tokens=10, output_tokens=4)
        self.assertEqual(tool.to_dict()["tool"], "terminal")
        self.assertEqual(answer.to_dict()["output_tokens"], 4)

    def test_capability_call_record_serializes(self) -> None:
        record = CapabilityCallRecord(
            capability_id="terminal",
            capability_type="tool",
            call_id="cap-1",
            status="success",
            session_id="session-1",
            latency_ms=12,
            payload={"text": "ok"},
        )
        self.assertEqual(record.to_dict()["capability_type"], "tool")
        self.assertEqual(record.to_dict()["latency_ms"], 12)

    def test_guard_result_serializes(self) -> None:
        result = GuardResult(name="grounding_guard", passed=False, reason="unsupported number", details={"numbers": ["123"]})
        self.assertEqual(result.to_dict()["details"]["numbers"], ["123"])

    def test_failed_run_outcome_requires_error_message(self) -> None:
        with self.assertRaises(ValueError):
            RunOutcome(status="failed")

    def test_harness_event_serializes(self) -> None:
        event = HarnessEvent(
            event_id="evt-1",
            run_id="run-1",
            name="run.started",
            ts="2026-04-02T12:00:00Z",
            payload={"route": "knowledge_qa"},
        )
        self.assertEqual(event.to_dict()["name"], "run.started")

    def test_harness_event_rejects_unknown_name(self) -> None:
        with self.assertRaises(ValueError):
            HarnessEvent(
                event_id="evt-1",
                run_id="run-1",
                name="token",  # type: ignore[arg-type]
                ts="2026-04-02T12:00:00Z",
                payload={},
            )


if __name__ == "__main__":
    unittest.main()
