from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ContextPathKind = Literal["direct_answer", "capability_path", "knowledge_qa", "resumed_hitl", "recovery_path"]
MemoryKind = Literal["semantic", "procedural", "episodic"]
MemoryType = Literal[
    "user_profile",
    "preference_feedback",
    "project_fact",
    "external_reference",
    "workflow_rule",
    "capability_lesson",
    "artifact_map",
    "session_episode",
]
MemoryScope = Literal["user", "project", "thread", "global"]
MemoryStatus = Literal["active", "stale", "superseded", "dropped", "invalidated"]
FreshnessState = Literal["fresh", "aging", "stale"]


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
    conversation_recall: int
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
    conversation_block: str = ""
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
            "conversation_block": self.conversation_block,
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
    selected_conversation_ids: tuple[str, ...] = ()
    dropped_items: tuple[str, ...] = ()
    truncation_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["selected_history_ids"] = list(self.selected_history_ids)
        payload["selected_memory_ids"] = list(self.selected_memory_ids)
        payload["selected_artifact_ids"] = list(self.selected_artifact_ids)
        payload["selected_evidence_ids"] = list(self.selected_evidence_ids)
        payload["selected_conversation_ids"] = list(self.selected_conversation_ids)
        payload["dropped_items"] = list(self.dropped_items)
        return payload


@dataclass(frozen=True)
class MemoryManifest:
    memory_id: str
    kind: MemoryKind
    namespace: str
    memory_type: MemoryType
    scope: MemoryScope
    title: str
    summary: str
    tags: tuple[str, ...] = ()
    source: str = ""
    created_at: str = ""
    updated_at: str = ""
    confidence: float = 0.0
    freshness: FreshnessState = "fresh"
    stale_after: str = ""
    status: MemoryStatus = "active"
    supersedes: tuple[str, ...] = ()
    applicability: dict[str, Any] = field(default_factory=dict)
    direct_prompt: bool = False
    promotion_priority: int = 0
    conflict_flag: bool = False
    conflict_with: tuple[str, ...] = ()
    source_turn_ids: tuple[str, ...] = ()
    source_run_ids: tuple[str, ...] = ()
    source_memory_ids: tuple[str, ...] = ()
    generated_by: str = ""
    generated_at: str = ""
    fingerprint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        payload["supersedes"] = list(self.supersedes)
        payload["conflict_with"] = list(self.conflict_with)
        payload["source_turn_ids"] = list(self.source_turn_ids)
        payload["source_run_ids"] = list(self.source_run_ids)
        payload["source_memory_ids"] = list(self.source_memory_ids)
        payload["applicability"] = dict(self.applicability)
        payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class StoredMemory(MemoryManifest):
    content: str = ""
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["content"] = self.content
        payload["enabled"] = self.enabled
        return payload

    def to_manifest(self) -> MemoryManifest:
        return MemoryManifest(
            memory_id=self.memory_id,
            kind=self.kind,
            namespace=self.namespace,
            memory_type=self.memory_type,
            scope=self.scope,
            title=self.title,
            summary=self.summary,
            tags=self.tags,
            source=self.source,
            created_at=self.created_at,
            updated_at=self.updated_at,
            confidence=self.confidence,
            freshness=self.freshness,
            stale_after=self.stale_after,
            status=self.status,
            supersedes=self.supersedes,
            applicability=self.applicability,
            direct_prompt=self.direct_prompt,
            promotion_priority=self.promotion_priority,
            conflict_flag=self.conflict_flag,
            conflict_with=self.conflict_with,
            source_turn_ids=self.source_turn_ids,
            source_run_ids=self.source_run_ids,
            source_memory_ids=self.source_memory_ids,
            generated_by=self.generated_by,
            generated_at=self.generated_at,
            fingerprint=self.fingerprint,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class MemoryCandidate:
    kind: MemoryKind
    memory_type: MemoryType
    scope: MemoryScope
    namespace: str
    title: str
    content: str
    summary: str
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    created_at: str = ""
    updated_at: str = ""
    confidence: float = 0.5
    stale_after: str = ""
    status: MemoryStatus = "active"
    supersedes: tuple[str, ...] = ()
    applicability: dict[str, Any] = field(default_factory=dict)
    direct_prompt: bool = False
    promotion_priority: int = 0
    source_turn_ids: tuple[str, ...] = ()
    source_run_ids: tuple[str, ...] = ()
    source_memory_ids: tuple[str, ...] = ()
    generated_by: str = ""
    generated_at: str = ""
    fingerprint: str = ""
    conflict_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        payload["metadata"] = dict(self.metadata)
        payload["supersedes"] = list(self.supersedes)
        payload["source_turn_ids"] = list(self.source_turn_ids)
        payload["source_run_ids"] = list(self.source_run_ids)
        payload["source_memory_ids"] = list(self.source_memory_ids)
        payload["applicability"] = dict(self.applicability)
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
    conversation_block: str = ""
    artifacts_block: str = ""
    retrieval_block: str = ""
    budget: SlotBudget = field(default_factory=lambda: SlotBudget(0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
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
            "conversation_block": self.conversation_block,
            "artifacts_block": self.artifacts_block,
            "retrieval_block": self.retrieval_block,
            "budget": self.budget.to_dict(),
            "budget_used": dict(self.budget_used),
            "excluded_from_prompt": list(self.excluded_from_prompt),
        }


@dataclass(frozen=True)
class ContextTurnSnapshot:
    turn_id: str
    session_id: str | None
    run_id: str
    thread_id: str
    assistant_message_id: str | None
    segment_index: int
    call_site: str
    path_type: ContextPathKind
    user_query: str
    context_envelope: ContextEnvelope
    assembly_decision: ContextAssemblyDecision
    budget_report: dict[str, Any] = field(default_factory=dict)
    selected_memory_ids: tuple[str, ...] = ()
    selected_artifact_ids: tuple[str, ...] = ()
    selected_evidence_ids: tuple[str, ...] = ()
    selected_conversation_ids: tuple[str, ...] = ()
    dropped_items: tuple[str, ...] = ()
    truncation_reason: str = ""
    run_status: str = "fresh"
    resume_source: str = ""
    checkpoint_id: str = ""
    orchestration_engine: str = "langgraph"
    model_invoked: bool = True
    excluded_from_context: bool = False
    excluded_at: str = ""
    exclusion_reason: str = ""
    call_ids: tuple[str, ...] = ()
    post_turn_state_snapshot: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "assistant_message_id": self.assistant_message_id,
            "segment_index": self.segment_index,
            "call_site": self.call_site,
            "path_type": self.path_type,
            "user_query": self.user_query,
            "context_envelope": self.context_envelope.to_dict(),
            "assembly_decision": self.assembly_decision.to_dict(),
            "budget_report": dict(self.budget_report),
            "selected_memory_ids": list(self.selected_memory_ids),
            "selected_artifact_ids": list(self.selected_artifact_ids),
            "selected_evidence_ids": list(self.selected_evidence_ids),
            "selected_conversation_ids": list(self.selected_conversation_ids),
            "dropped_items": list(self.dropped_items),
            "truncation_reason": self.truncation_reason,
            "run_status": self.run_status,
            "resume_source": self.resume_source,
            "checkpoint_id": self.checkpoint_id,
            "orchestration_engine": self.orchestration_engine,
            "model_invoked": self.model_invoked,
            "excluded_from_context": self.excluded_from_context,
            "excluded_at": self.excluded_at,
            "exclusion_reason": self.exclusion_reason,
            "call_ids": list(self.call_ids),
            "post_turn_state_snapshot": dict(self.post_turn_state_snapshot),
            "created_at": self.created_at,
        }

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "assistant_message_id": self.assistant_message_id,
            "segment_index": self.segment_index,
            "call_site": self.call_site,
            "path_type": self.path_type,
            "user_query": self.user_query,
            "budget_report": dict(self.budget_report),
            "selected_memory_ids": list(self.selected_memory_ids),
            "selected_artifact_ids": list(self.selected_artifact_ids),
            "selected_evidence_ids": list(self.selected_evidence_ids),
            "selected_conversation_ids": list(self.selected_conversation_ids),
            "dropped_items": list(self.dropped_items),
            "truncation_reason": self.truncation_reason,
            "run_status": self.run_status,
            "resume_source": self.resume_source,
            "checkpoint_id": self.checkpoint_id,
            "orchestration_engine": self.orchestration_engine,
            "model_invoked": self.model_invoked,
            "excluded_from_context": self.excluded_from_context,
            "excluded_at": self.excluded_at,
            "exclusion_reason": self.exclusion_reason,
            "call_ids": list(self.call_ids),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ContextModelCallSnapshot:
    call_id: str
    session_id: str | None
    run_id: str
    thread_id: str
    turn_id: str
    call_type: str
    call_site: str
    path_type: ContextPathKind
    user_query: str
    context_envelope: ContextEnvelope
    assembly_decision: ContextAssemblyDecision
    budget_report: dict[str, Any] = field(default_factory=dict)
    selected_memory_ids: tuple[str, ...] = ()
    selected_artifact_ids: tuple[str, ...] = ()
    selected_evidence_ids: tuple[str, ...] = ()
    selected_conversation_ids: tuple[str, ...] = ()
    dropped_items: tuple[str, ...] = ()
    truncation_reason: str = ""
    run_status: str = "fresh"
    resume_source: str = ""
    checkpoint_id: str = ""
    orchestration_engine: str = "langgraph"
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "call_type": self.call_type,
            "call_site": self.call_site,
            "path_type": self.path_type,
            "user_query": self.user_query,
            "context_envelope": self.context_envelope.to_dict(),
            "assembly_decision": self.assembly_decision.to_dict(),
            "budget_report": dict(self.budget_report),
            "selected_memory_ids": list(self.selected_memory_ids),
            "selected_artifact_ids": list(self.selected_artifact_ids),
            "selected_evidence_ids": list(self.selected_evidence_ids),
            "selected_conversation_ids": list(self.selected_conversation_ids),
            "dropped_items": list(self.dropped_items),
            "truncation_reason": self.truncation_reason,
            "run_status": self.run_status,
            "resume_source": self.resume_source,
            "checkpoint_id": self.checkpoint_id,
            "orchestration_engine": self.orchestration_engine,
            "created_at": self.created_at,
        }

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "turn_id": self.turn_id,
            "call_type": self.call_type,
            "call_site": self.call_site,
            "path_type": self.path_type,
            "run_status": self.run_status,
            "resume_source": self.resume_source,
            "checkpoint_id": self.checkpoint_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ConversationRecallRecord:
    chunk_id: str
    thread_id: str
    session_id: str | None
    run_id: str
    role: str
    source_message_id: str
    snippet: str
    summary: str
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    source_turn_ids: tuple[str, ...] = ()
    source_run_ids: tuple[str, ...] = ()
    source_memory_ids: tuple[str, ...] = ()
    generated_by: str = ""
    generated_at: str = ""
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        payload["metadata"] = dict(self.metadata)
        payload["source_turn_ids"] = list(self.source_turn_ids)
        payload["source_run_ids"] = list(self.source_run_ids)
        payload["source_memory_ids"] = list(self.source_memory_ids)
        return payload


@dataclass(frozen=True)
class ContextAuditRecord:
    audit_id: str
    event_type: str
    session_id: str | None
    thread_id: str
    run_id: str = ""
    turn_id: str = ""
    created_at: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "event_type": self.event_type,
            "session_id": self.session_id,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "created_at": self.created_at,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class ConsolidationRunSummary:
    consolidation_id: str
    trigger: str
    thread_id: str | None
    status: str
    created_at: str
    completed_at: str = ""
    promoted_memory_ids: tuple[str, ...] = ()
    superseded_memory_ids: tuple[str, ...] = ()
    stale_memory_ids: tuple[str, ...] = ()
    dropped_memory_ids: tuple[str, ...] = ()
    conflict_memory_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["promoted_memory_ids"] = list(self.promoted_memory_ids)
        payload["superseded_memory_ids"] = list(self.superseded_memory_ids)
        payload["stale_memory_ids"] = list(self.stale_memory_ids)
        payload["dropped_memory_ids"] = list(self.dropped_memory_ids)
        payload["conflict_memory_ids"] = list(self.conflict_memory_ids)
        payload["notes"] = list(self.notes)
        payload["stats"] = dict(self.stats)
        return payload
