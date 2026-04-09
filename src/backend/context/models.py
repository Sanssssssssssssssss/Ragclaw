from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ContextPathKind = Literal["direct_answer", "capability_path", "knowledge_qa", "resumed_hitl", "recovery_path"]
MemoryKind = Literal["semantic", "procedural"]


@dataclass(frozen=True)
class WorkingMemory:
    thread_id: str
    current_goal: str
    active_constraints: tuple[str, ...] = ()
    active_entities: tuple[str, ...] = ()
    active_artifacts: tuple[str, ...] = ()
    latest_capability_results: tuple[str, ...] = ()
    latest_retrieval_summary: str = ""
    latest_user_intent: str = ""
    unresolved_items: tuple[str, ...] = ()
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["active_constraints"] = list(self.active_constraints)
        payload["active_entities"] = list(self.active_entities)
        payload["active_artifacts"] = list(self.active_artifacts)
        payload["latest_capability_results"] = list(self.latest_capability_results)
        payload["unresolved_items"] = list(self.unresolved_items)
        return payload


@dataclass(frozen=True)
class EpisodicSummary:
    thread_id: str = ""
    summary_version: int = 1
    key_facts: tuple[str, ...] = ()
    completed_subtasks: tuple[str, ...] = ()
    rejected_paths: tuple[str, ...] = ()
    important_decisions: tuple[str, ...] = ()
    important_artifacts: tuple[str, ...] = ()
    open_loops: tuple[str, ...] = ()
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["key_facts"] = list(self.key_facts)
        payload["completed_subtasks"] = list(self.completed_subtasks)
        payload["rejected_paths"] = list(self.rejected_paths)
        payload["important_decisions"] = list(self.important_decisions)
        payload["important_artifacts"] = list(self.important_artifacts)
        payload["open_loops"] = list(self.open_loops)
        return payload


@dataclass(frozen=True)
class SlotBudget:
    system: int
    recent_history: int
    working_memory: int
    episodic_summary: int
    semantic_memory: int
    procedural_memory: int
    artifacts: int
    retrieval_evidence: int
    answer_reserve: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ContextEnvelope:
    system_block: str = ""
    history_block: str = ""
    working_memory_block: str = ""
    episodic_block: str = ""
    semantic_block: str = ""
    procedural_block: str = ""
    artifact_block: str = ""
    evidence_block: str = ""
    budget_report: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "system_block": self.system_block,
            "history_block": self.history_block,
            "working_memory_block": self.working_memory_block,
            "episodic_block": self.episodic_block,
            "semantic_block": self.semantic_block,
            "procedural_block": self.procedural_block,
            "artifact_block": self.artifact_block,
            "evidence_block": self.evidence_block,
            "budget_report": dict(self.budget_report),
        }


@dataclass(frozen=True)
class ContextAssemblyDecision:
    path_type: ContextPathKind
    selected_history_ids: tuple[str, ...] = ()
    selected_memory_ids: tuple[str, ...] = ()
    selected_artifact_ids: tuple[str, ...] = ()
    selected_evidence_ids: tuple[str, ...] = ()
    dropped_items: tuple[str, ...] = ()
    truncation_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["selected_history_ids"] = list(self.selected_history_ids)
        payload["selected_memory_ids"] = list(self.selected_memory_ids)
        payload["selected_artifact_ids"] = list(self.selected_artifact_ids)
        payload["selected_evidence_ids"] = list(self.selected_evidence_ids)
        payload["dropped_items"] = list(self.dropped_items)
        return payload


@dataclass(frozen=True)
class StoredMemory:
    memory_id: str
    kind: MemoryKind
    namespace: str
    title: str
    content: str
    summary: str = ""
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    created_at: str = ""
    updated_at: str = ""
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class ContextAssembly:
    path_kind: ContextPathKind
    history_messages: tuple[dict[str, str], ...]
    envelope: ContextEnvelope
    decision: ContextAssemblyDecision
    extra_instructions: tuple[str, ...] = ()
    working_memory_block: str = ""
    episodic_block: str = ""
    semantic_block: str = ""
    procedural_block: str = ""
    artifacts_block: str = ""
    retrieval_block: str = ""
    budget: SlotBudget = field(default_factory=lambda: SlotBudget(0, 0, 0, 0, 0, 0, 0, 0, 0))
    budget_used: dict[str, int] = field(default_factory=dict)
    excluded_from_prompt: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_kind": self.path_kind,
            "history_messages": [dict(item) for item in self.history_messages],
            "envelope": self.envelope.to_dict(),
            "decision": self.decision.to_dict(),
            "extra_instructions": list(self.extra_instructions),
            "working_memory_block": self.working_memory_block,
            "episodic_block": self.episodic_block,
            "semantic_block": self.semantic_block,
            "procedural_block": self.procedural_block,
            "artifacts_block": self.artifacts_block,
            "retrieval_block": self.retrieval_block,
            "budget": self.budget.to_dict(),
            "budget_used": dict(self.budget_used),
            "excluded_from_prompt": list(self.excluded_from_prompt),
        }
