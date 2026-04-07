"""Stable harness event and record types for run lifecycle integration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from src.backend.capabilities.types import CapabilityType


HarnessEventName = Literal[
    "run.started",
    "run.queued",
    "run.dequeued",
    "checkpoint.created",
    "checkpoint.resumed",
    "checkpoint.interrupted",
    "hitl.requested",
    "hitl.approved",
    "hitl.rejected",
    "route.decided",
    "skill.decided",
    "capability.started",
    "capability.retry",
    "capability.completed",
    "capability.failed",
    "capability.blocked",
    "retrieval.started",
    "retrieval.completed",
    "tool.started",
    "tool.completed",
    "answer.started",
    "answer.delta",
    "answer.completed",
    "guard.failed",
    "run.completed",
    "run.failed",
]

CANONICAL_EVENT_NAMES: tuple[HarnessEventName, ...] = (
    "run.started",
    "run.queued",
    "run.dequeued",
    "checkpoint.created",
    "checkpoint.resumed",
    "checkpoint.interrupted",
    "hitl.requested",
    "hitl.approved",
    "hitl.rejected",
    "route.decided",
    "skill.decided",
    "capability.started",
    "capability.retry",
    "capability.completed",
    "capability.failed",
    "capability.blocked",
    "retrieval.started",
    "retrieval.completed",
    "tool.started",
    "tool.completed",
    "answer.started",
    "answer.delta",
    "answer.completed",
    "guard.failed",
    "run.completed",
    "run.failed",
)

RunSource = Literal["chat_api", "benchmark", "replay", "internal"]
RunStatus = Literal["completed", "failed"]
RetrievalKind = Literal["memory", "knowledge"]
RetrievalChannel = Literal["memory", "skill", "vector", "bm25", "fused"]


@dataclass(frozen=True)
class RunMetadata:
    run_id: str
    session_id: str | None = None
    thread_id: str | None = None
    user_message: str = ""
    source: RunSource = "chat_api"
    started_at: str = ""
    orchestration_engine: str = ""
    checkpoint_id: str = ""
    resume_source: str = ""
    run_status: str = "fresh"

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RouteDecisionRecord:
    intent: str
    needs_tools: bool
    needs_retrieval: bool
    allowed_tools: tuple[str, ...] = ()
    confidence: float = 0.0
    reason_short: str = ""
    source: str = ""
    subtype: str = ""
    ambiguity_flags: tuple[str, ...] = ()
    escalated: bool = False
    model_name: str = ""

    def __post_init__(self) -> None:
        if not self.intent.strip():
            raise ValueError("intent must not be empty")
        if self.intent in {"direct_answer", "knowledge_qa"} and self.allowed_tools:
            raise ValueError(f"{self.intent} must not expose tools")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed_tools"] = list(self.allowed_tools)
        payload["ambiguity_flags"] = list(self.ambiguity_flags)
        return payload


@dataclass(frozen=True)
class SkillDecisionRecord:
    use_skill: bool
    skill_name: str = ""
    confidence: float = 0.0
    reason_short: str = ""

    def __post_init__(self) -> None:
        if self.use_skill and not self.skill_name.strip():
            raise ValueError("skill_name is required when use_skill is true")
        if not self.use_skill and self.skill_name.strip():
            raise ValueError("skill_name must be empty when use_skill is false")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalEvidenceRecord:
    source_path: str
    source_type: str
    locator: str
    snippet: str
    channel: RetrievalChannel
    score: float | None = None
    parent_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalRecord:
    kind: RetrievalKind
    stage: str
    title: str
    message: str = ""
    results: tuple[RetrievalEvidenceRecord, ...] = ()
    status: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.stage.strip():
            raise ValueError("retrieval stage must not be empty")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["results"] = [item.to_dict() for item in self.results]
        return payload


@dataclass(frozen=True)
class ToolCallRecord:
    tool: str
    input: str = ""
    output: str = ""
    call_id: str = ""

    def __post_init__(self) -> None:
        if not self.tool.strip():
            raise ValueError("tool name must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityCallRecord:
    capability_id: str
    capability_type: CapabilityType
    call_id: str
    status: str
    session_id: str | None = None
    retry_count: int = 0
    partial: bool = False
    latency_ms: int = 0
    error_type: str = ""
    error_message: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    display_name: str = ""
    risk_level: str = ""
    approval_required: bool = False
    budget_cost: int = 0

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be empty")
        if not self.call_id.strip():
            raise ValueError("call_id must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnswerRecord:
    content: str = ""
    segment_index: int = 0
    final: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.segment_index < 0:
            raise ValueError("segment_index must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GuardResult:
    name: str
    passed: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("guard name must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "reason": self.reason,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class RunOutcome:
    status: RunStatus
    final_answer: str = ""
    route_intent: str = ""
    used_skill: str = ""
    tool_names: tuple[str, ...] = ()
    retrieval_sources: tuple[str, ...] = ()
    error_message: str = ""
    completed_at: str = ""
    thread_id: str | None = None
    orchestration_engine: str = ""
    checkpoint_id: str = ""
    resume_source: str = ""
    run_status: str = "fresh"

    def __post_init__(self) -> None:
        if self.status == "failed" and not self.error_message.strip():
            raise ValueError("failed outcome requires error_message")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tool_names"] = list(self.tool_names)
        payload["retrieval_sources"] = list(self.retrieval_sources)
        return payload


@dataclass(frozen=True)
class HarnessEvent:
    event_id: str
    run_id: str
    name: HarnessEventName
    ts: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if self.name not in CANONICAL_EVENT_NAMES:
            raise ValueError(f"unsupported event name: {self.name}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "name": self.name,
            "ts": self.ts,
            "payload": dict(self.payload),
        }
