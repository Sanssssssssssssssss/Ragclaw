"""Compatibility adapters for shadow-writing harness traces from legacy chat streams."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.runtime import HarnessRuntime, RuntimeDependencies
from harness.trace_store import RunTraceStore
from harness.types import (
    AnswerRecord,
    RetrievalEvidenceRecord,
    RetrievalRecord,
    RouteDecisionRecord,
    SkillDecisionRecord,
    ToolCallRecord,
)

INTERNAL_ROUTE_EVENT = "_harness_route"
INTERNAL_SKILL_EVENT = "_harness_skill"
_VALID_CHANNELS = {"memory", "skill", "vector", "bm25", "fused"}
_VALID_RETRIEVAL_KINDS = {"memory", "knowledge"}


@dataclass
class ChatTraceShadowState:
    route_intent: str = ""
    used_skill: str = ""
    tool_names: list[str] = field(default_factory=list)
    retrieval_sources: list[str] = field(default_factory=list)
    answer_started: bool = False
    segment_index: int = 0
    final_answer: str = ""

    def add_tool_name(self, tool_name: str) -> None:
        tool = str(tool_name or "").strip()
        if tool and tool not in self.tool_names:
            self.tool_names.append(tool)

    def add_retrieval_source(self, source_path: str) -> None:
        source = str(source_path or "").strip()
        if source and source not in self.retrieval_sources:
            self.retrieval_sources.append(source)


def build_chat_runtime(base_dir: Path) -> HarnessRuntime:
    trace_store = RunTraceStore(Path(base_dir) / "storage" / "runs")
    return HarnessRuntime(RuntimeDependencies(trace_store=trace_store))


def _normalize_channel(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in _VALID_CHANNELS else "fused"


def _normalize_retrieval_kind(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in _VALID_RETRIEVAL_KINDS else "knowledge"


def _record_answer_started_if_needed(
    runtime: HarnessRuntime,
    run_id: str,
    state: ChatTraceShadowState,
) -> None:
    if state.answer_started:
        return
    runtime.record_event(
        run_id,
        "answer.started",
        AnswerRecord(content="", segment_index=state.segment_index, final=False).to_dict(),
    )
    state.answer_started = True


def consume_legacy_chat_event(
    runtime: HarnessRuntime,
    run_id: str,
    event: dict[str, Any],
    state: ChatTraceShadowState,
) -> bool:
    event_type = str(event.get("type", "") or "")

    if event_type == INTERNAL_ROUTE_EVENT:
        decision = event.get("decision", {}) or {}
        record = RouteDecisionRecord(
            intent=str(decision.get("intent", "") or "").strip(),
            needs_tools=bool(decision.get("needs_tools", False)),
            needs_retrieval=bool(decision.get("needs_retrieval", False)),
            allowed_tools=tuple(str(item).strip() for item in decision.get("allowed_tools", []) or [] if str(item).strip()),
            confidence=float(decision.get("confidence", 0.0) or 0.0),
            reason_short=str(decision.get("reason_short", "") or ""),
            source=str(decision.get("source", "") or ""),
            subtype=str(decision.get("subtype", "") or ""),
            ambiguity_flags=tuple(
                str(item).strip() for item in decision.get("ambiguity_flags", []) or [] if str(item).strip()
            ),
            escalated=bool(decision.get("escalated", False)),
            model_name=str(decision.get("model_name", "") or ""),
        )
        state.route_intent = record.intent
        runtime.record_event(run_id, "route.decided", record.to_dict())
        return False

    if event_type == INTERNAL_SKILL_EVENT:
        decision = event.get("decision", {}) or {}
        record = SkillDecisionRecord(
            use_skill=bool(decision.get("use_skill", False)),
            skill_name=str(decision.get("skill_name", "") or ""),
            confidence=float(decision.get("confidence", 0.0) or 0.0),
            reason_short=str(decision.get("reason_short", "") or ""),
        )
        if record.use_skill:
            state.used_skill = record.skill_name
        runtime.record_event(run_id, "skill.decided", record.to_dict())
        return False

    if event_type == "retrieval":
        results = []
        for item in event.get("results", []) or []:
            if not isinstance(item, dict):
                continue
            source_path = str(item.get("source_path", "") or "").strip()
            if source_path:
                state.add_retrieval_source(source_path)
            score = item.get("score")
            results.append(
                RetrievalEvidenceRecord(
                    source_path=source_path,
                    source_type=str(item.get("source_type", "") or "").strip(),
                    locator=str(item.get("locator", "") or "").strip(),
                    snippet=str(item.get("snippet", "") or ""),
                    channel=_normalize_channel(item.get("channel", "fused")),
                    score=float(score or 0.0) if score is not None else None,
                    parent_id=str(item.get("parent_id", "") or "").strip() or None,
                )
            )
        record = RetrievalRecord(
            kind=_normalize_retrieval_kind(event.get("kind", "knowledge")),
            stage=str(event.get("stage", "unknown") or "unknown"),
            title=str(event.get("title", "") or "retrieval"),
            message=str(event.get("message", "") or ""),
            results=tuple(results),
            status=str(event.get("status", "") or ""),
            reason=str(event.get("reason", "") or ""),
        )
        runtime.record_event(run_id, "retrieval.completed", record.to_dict())
        return True

    if event_type == "tool_start":
        tool_name = str(event.get("tool", "tool") or "tool")
        state.add_tool_name(tool_name)
        runtime.record_event(
            run_id,
            "tool.started",
            ToolCallRecord(tool=tool_name, input=str(event.get("input", "") or "")).to_dict(),
        )
        return True

    if event_type == "tool_end":
        tool_name = str(event.get("tool", "tool") or "tool")
        state.add_tool_name(tool_name)
        runtime.record_event(
            run_id,
            "tool.completed",
            ToolCallRecord(
                tool=tool_name,
                input=str(event.get("input", "") or ""),
                output=str(event.get("output", "") or ""),
            ).to_dict(),
        )
        return True

    if event_type == "new_response":
        state.segment_index += 1
        state.answer_started = False
        return True

    if event_type == "token":
        content = str(event.get("content", "") or "")
        if not content:
            return True
        _record_answer_started_if_needed(runtime, run_id, state)
        runtime.record_event(
            run_id,
            "answer.delta",
            AnswerRecord(content=content, segment_index=state.segment_index, final=False).to_dict(),
        )
        state.final_answer += content
        return True

    if event_type == "done":
        content = str(event.get("content", "") or "").strip()
        usage = event.get("usage", {}) or {}
        if content and not state.answer_started:
            _record_answer_started_if_needed(runtime, run_id, state)
            runtime.record_event(
                run_id,
                "answer.delta",
                AnswerRecord(content=content, segment_index=state.segment_index, final=False).to_dict(),
            )
        if content:
            state.final_answer = content
        runtime.record_event(
            run_id,
            "answer.completed",
            AnswerRecord(
                content=state.final_answer,
                segment_index=state.segment_index,
                final=True,
                input_tokens=int(usage.get("input_tokens", 0) or 0) if usage.get("input_tokens") is not None else None,
                output_tokens=int(usage.get("output_tokens", 0) or 0) if usage.get("output_tokens") is not None else None,
            ).to_dict(),
        )
        return True

    return True
