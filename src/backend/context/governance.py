from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.backend.context.models import MemoryCandidate, MemoryKind, MemoryScope, MemoryType
from src.backend.context.policies import (
    conflict_key_for,
    fingerprint_for,
    infer_reference_tags,
    looks_like_artifact_map,
    looks_like_external_reference,
    looks_like_feedback,
    looks_like_project_fact,
    looks_like_user_profile,
    project_namespace,
    stale_after_from,
    thread_namespace,
    user_namespace,
)


_NOISY_OUTPUT_PATTERN = re.compile(
    r"\b(trace|checkpoint|audit payload|stack trace|stderr|stdout|exception blob|tool output)\b",
    re.IGNORECASE,
)
_TEMPORARY_FAILURE_PATTERN = re.compile(
    r"\b(timeout|temporary|transient|retrying|failed once|one-off|flaky)\b",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


@dataclass(frozen=True)
class MemoryGovernanceRule:
    memory_type: MemoryType
    kind: MemoryKind
    scope: MemoryScope
    stale_days: int
    promotion_priority: int
    direct_prompt: bool
    retrieval_only: bool
    immediate_write: bool
    promotion_threshold: int
    allow_title_prefix: str


RULES: dict[MemoryType, MemoryGovernanceRule] = {
    "user_profile": MemoryGovernanceRule("user_profile", "semantic", "user", 180, 90, True, False, True, 1, "User profile"),
    "preference_feedback": MemoryGovernanceRule("preference_feedback", "procedural", "user", 90, 100, True, False, True, 1, "Preference"),
    "project_fact": MemoryGovernanceRule("project_fact", "semantic", "project", 30, 80, True, False, True, 1, "Project fact"),
    "external_reference": MemoryGovernanceRule("external_reference", "semantic", "project", 120, 70, True, False, True, 1, "External reference"),
    "workflow_rule": MemoryGovernanceRule("workflow_rule", "procedural", "project", 120, 95, True, False, True, 1, "Workflow rule"),
    "capability_lesson": MemoryGovernanceRule("capability_lesson", "procedural", "project", 45, 75, False, False, False, 2, "Capability lesson"),
    "artifact_map": MemoryGovernanceRule("artifact_map", "semantic", "project", 60, 60, False, False, False, 2, "Artifact map"),
    "session_episode": MemoryGovernanceRule("session_episode", "episodic", "thread", 5, 20, False, True, True, 1, "Session episode"),
}


def rule_for(memory_type: MemoryType) -> MemoryGovernanceRule:
    return RULES[memory_type]


def memory_scope_namespace(scope: MemoryScope, *, base_dir: Path | None, thread_id: str) -> str:
    if scope == "user":
        return user_namespace()
    if scope == "project":
        return project_namespace(base_dir)
    if scope == "global":
        return "global:default"
    return thread_namespace(thread_id)


def _sanitize_text(text: str, *, limit: int = 500) -> str:
    normalized = " ".join(str(text or "").split()).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + " ..."


def _candidate(
    *,
    memory_type: MemoryType,
    base_dir: Path | None,
    thread_id: str,
    title: str,
    content: str,
    summary: str,
    tags: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
    source: str,
    updated_at: str,
    confidence: float,
    applicability: dict[str, Any] | None = None,
    source_turn_ids: tuple[str, ...] = (),
    source_run_ids: tuple[str, ...] = (),
    source_memory_ids: tuple[str, ...] = (),
    generated_by: str = "context_writer",
) -> MemoryCandidate:
    rule = rule_for(memory_type)
    namespace = memory_scope_namespace(rule.scope, base_dir=base_dir, thread_id=thread_id)
    content_value = _sanitize_text(content, limit=1200 if memory_type == "session_episode" else 500)
    summary_value = _sanitize_text(summary, limit=180)
    fingerprint = fingerprint_for(rule.kind, namespace, content_value, tags)
    return MemoryCandidate(
        kind=rule.kind,
        memory_type=memory_type,
        scope=rule.scope,
        namespace=namespace,
        title=title,
        content=content_value,
        summary=summary_value,
        tags=tags,
        metadata=dict(metadata or {}),
        source=source,
        created_at=updated_at,
        updated_at=updated_at,
        confidence=max(0.0, min(1.0, confidence)),
        stale_after=stale_after_from(updated_at, days=rule.stale_days),
        status="active",
        applicability=dict(applicability or {}),
        direct_prompt=rule.direct_prompt and not rule.retrieval_only,
        promotion_priority=rule.promotion_priority,
        source_turn_ids=source_turn_ids,
        source_run_ids=source_run_ids,
        source_memory_ids=source_memory_ids,
        generated_by=generated_by,
        generated_at=updated_at,
        fingerprint=fingerprint,
        conflict_key=conflict_key_for(memory_type, namespace, title),
    )


def _is_forbidden_long_term(candidate: MemoryCandidate) -> bool:
    if candidate.memory_type == "session_episode":
        return False
    source = str(candidate.source or "").strip().lower()
    content = f"{candidate.title}\n{candidate.content}\n{candidate.summary}".strip()
    metadata = dict(candidate.metadata)
    if metadata.get("derivable_from_repo"):
        return True
    if metadata.get("raw_tool_output") or metadata.get("raw_trace") or metadata.get("raw_checkpoint") or metadata.get("raw_hitl"):
        return True
    if source in {"raw_trace", "raw_checkpoint", "raw_hitl", "tool_output"}:
        return True
    if _NOISY_OUTPUT_PATTERN.search(content):
        return True
    if metadata.get("transient_failure") and candidate.memory_type not in {"capability_lesson"}:
        return True
    if _TEMPORARY_FAILURE_PATTERN.search(content) and candidate.memory_type in {"project_fact", "artifact_map"}:
        return True
    if len(candidate.content.strip()) < 24:
        return True
    return False


def _dedupe_candidates(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    ordered: list[MemoryCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.fingerprint in seen:
            continue
        seen.add(candidate.fingerprint)
        ordered.append(candidate)
    return ordered


def extract_memory_candidates(
    *,
    state: dict[str, Any],
    working_memory,
    episodic_summary,
    base_dir: Path | None,
    updated_at: str,
) -> list[MemoryCandidate]:
    thread_id = str(working_memory.thread_id or state.get("thread_id", "") or state.get("session_id", "") or "")
    if not thread_id or not updated_at:
        return []

    user_message = str(state.get("user_message", "") or "").strip()
    history = list(state.get("history", []) or [])
    turn_id = str(state.get("turn_id", "") or "").strip()
    run_id = str(state.get("run_id", "") or "").strip()
    source_turn_ids = (turn_id,) if turn_id else ()
    source_run_ids = (run_id,) if run_id else ()
    source_memory_ids = tuple(str(item) for item in state.get("selected_memory_ids", []) or [] if str(item).strip())
    recent_user_lines = [
        str(item.get("content", "") or "").strip()
        for item in history[-6:]
        if isinstance(item, dict) and str(item.get("role", "") or "").strip() == "user" and str(item.get("content", "") or "").strip()
    ]
    if user_message:
        recent_user_lines.append(user_message)

    candidates: list[MemoryCandidate] = []

    for line in recent_user_lines:
        if looks_like_user_profile(line):
            candidates.append(
                _candidate(
                    memory_type="user_profile",
                    base_dir=base_dir,
                    thread_id=thread_id,
                    title="User profile signal",
                    content=line,
                    summary=line,
                    tags=("user", "profile"),
                    source="user_message",
                    updated_at=updated_at,
                    confidence=0.92,
                    applicability={"prompt_paths": ["direct_answer", "capability_path", "knowledge_qa"]},
                    source_turn_ids=source_turn_ids,
                    source_run_ids=source_run_ids,
                    source_memory_ids=source_memory_ids,
                )
            )
        if looks_like_feedback(line):
            candidates.append(
                _candidate(
                    memory_type="preference_feedback",
                    base_dir=base_dir,
                    thread_id=thread_id,
                    title="User preference feedback",
                    content=line,
                    summary=line,
                    tags=("preference", "feedback"),
                    source="user_message",
                    updated_at=updated_at,
                    confidence=0.9,
                    applicability={"prompt_paths": ["direct_answer", "capability_path", "knowledge_qa", "resumed_hitl"]},
                    source_turn_ids=source_turn_ids,
                    source_run_ids=source_run_ids,
                    source_memory_ids=source_memory_ids,
                )
            )
        if looks_like_project_fact(line):
            candidates.append(
                _candidate(
                    memory_type="project_fact",
                    base_dir=base_dir,
                    thread_id=thread_id,
                    title="Project fact",
                    content=line,
                    summary=line,
                    tags=("project", "fact"),
                    source="user_message",
                    updated_at=updated_at,
                    confidence=0.82,
                    applicability={"prompt_paths": ["capability_path", "knowledge_qa", "recovery_path"]},
                    source_turn_ids=source_turn_ids,
                    source_run_ids=source_run_ids,
                    source_memory_ids=source_memory_ids,
                )
            )
        if looks_like_external_reference(line):
            candidates.append(
                _candidate(
                    memory_type="external_reference",
                    base_dir=base_dir,
                    thread_id=thread_id,
                    title="External reference",
                    content=line,
                    summary=line,
                    tags=("reference",) + infer_reference_tags(line),
                    source="user_message",
                    updated_at=updated_at,
                    confidence=0.86,
                    applicability={"prompt_paths": ["knowledge_qa", "capability_path"]},
                    source_turn_ids=source_turn_ids,
                    source_run_ids=source_run_ids,
                    source_memory_ids=source_memory_ids,
                )
            )
        if looks_like_artifact_map(line):
            candidates.append(
                _candidate(
                    memory_type="artifact_map",
                    base_dir=base_dir,
                    thread_id=thread_id,
                    title="Artifact map",
                    content=line,
                    summary=line,
                    tags=("artifact", "map"),
                    source="user_message",
                    updated_at=updated_at,
                    confidence=0.72,
                    applicability={"prompt_paths": ["knowledge_qa", "capability_path"]},
                    source_turn_ids=source_turn_ids,
                    source_run_ids=source_run_ids,
                    source_memory_ids=source_memory_ids,
                )
            )

    for constraint in list(working_memory.active_constraints)[:4]:
        text = str(constraint).strip()
        if not text:
            continue
        candidates.append(
            _candidate(
                memory_type="workflow_rule" if looks_like_feedback(text) else "workflow_rule",
                base_dir=base_dir,
                thread_id=thread_id,
                title="Workflow rule",
                content=text,
                summary=text,
                tags=("workflow", "rule"),
                source="working_memory",
                updated_at=updated_at,
                confidence=0.74,
                applicability={"prompt_paths": ["capability_path", "resumed_hitl", "recovery_path"]},
                source_turn_ids=source_turn_ids,
                source_run_ids=source_run_ids,
                source_memory_ids=source_memory_ids,
            )
        )

    for decision in list(episodic_summary.important_decisions)[:3]:
        text = str(decision).strip()
        if text:
            candidates.append(
                _candidate(
                    memory_type="workflow_rule",
                    base_dir=base_dir,
                    thread_id=thread_id,
                    title="Workflow rule",
                    content=text,
                    summary=text,
                    tags=("decision", "workflow"),
                    source="episodic_summary",
                    updated_at=updated_at,
                    confidence=0.68,
                    applicability={"prompt_paths": ["capability_path", "recovery_path", "resumed_hitl"]},
                    source_turn_ids=source_turn_ids,
                    source_run_ids=source_run_ids,
                    source_memory_ids=source_memory_ids,
                )
            )

    last_failure = state.get("last_failure")
    if isinstance(last_failure, dict) and last_failure:
        lesson = f"{last_failure.get('capability_id', 'capability')} failed with {last_failure.get('error_type', 'unknown')}"
        if state.get("recovery_action"):
            lesson += f"; recovery={state.get('recovery_action')}"
        candidates.append(
            _candidate(
                memory_type="capability_lesson",
                base_dir=base_dir,
                thread_id=thread_id,
                title="Capability lesson",
                content=lesson,
                summary=lesson,
                tags=("capability", "failure", "lesson"),
                metadata={"transient_failure": False},
                source="failure_state",
                updated_at=updated_at,
                confidence=0.66,
                applicability={"prompt_paths": ["capability_path", "recovery_path"]},
                source_turn_ids=source_turn_ids,
                source_run_ids=source_run_ids,
                source_memory_ids=source_memory_ids,
            )
        )

    for artifact in list(episodic_summary.important_artifacts)[:4]:
        text = str(artifact).strip()
        if not text:
            continue
        if _URL_PATTERN.search(text):
            candidates.append(
                _candidate(
                    memory_type="external_reference",
                    base_dir=base_dir,
                    thread_id=thread_id,
                    title="External reference",
                    content=text,
                    summary=text,
                    tags=("reference", "url"),
                    source="episodic_summary",
                    updated_at=updated_at,
                    confidence=0.7,
                    applicability={"prompt_paths": ["knowledge_qa"]},
                    source_turn_ids=source_turn_ids,
                    source_run_ids=source_run_ids,
                    source_memory_ids=source_memory_ids,
                )
            )
        elif looks_like_artifact_map(text):
            candidates.append(
                _candidate(
                    memory_type="artifact_map",
                    base_dir=base_dir,
                    thread_id=thread_id,
                    title="Artifact map",
                    content=text,
                    summary=text,
                    tags=("artifact", "map"),
                    metadata={"derivable_from_repo": False},
                    source="episodic_summary",
                    updated_at=updated_at,
                    confidence=0.65,
                    applicability={"prompt_paths": ["knowledge_qa", "capability_path"]},
                    source_turn_ids=source_turn_ids,
                    source_run_ids=source_run_ids,
                    source_memory_ids=source_memory_ids,
                )
            )

    episode_lines = [
        *[str(item).strip() for item in episodic_summary.key_facts[:3]],
        *[str(item).strip() for item in episodic_summary.important_decisions[:3]],
        *[str(item).strip() for item in episodic_summary.open_loops[:3]],
    ]
    episode_lines = [item for item in episode_lines if item]
    if episode_lines:
        stable_hints = [
            {
                "memory_type": candidate.memory_type,
                "title": candidate.title,
                "summary": candidate.summary,
                "namespace": candidate.namespace,
                "fingerprint": candidate.fingerprint,
                "conflict_key": candidate.conflict_key,
                "confidence": candidate.confidence,
                "direct_prompt": candidate.direct_prompt,
            }
            for candidate in candidates
            if candidate.memory_type != "session_episode"
        ]
        episode_content = " | ".join(episode_lines)
        candidates.append(
            _candidate(
                memory_type="session_episode",
                base_dir=base_dir,
                thread_id=thread_id,
                title="Session episode",
                content=episode_content,
                summary="; ".join(episode_lines[:2]),
                tags=("episode", "thread"),
                metadata={
                    "thread_id": thread_id,
                    "stable_candidates": stable_hints,
                    "completed_subtasks": list(episodic_summary.completed_subtasks),
                    "open_loops": list(episodic_summary.open_loops),
                },
                source="episodic_summary",
                updated_at=updated_at,
                confidence=0.78,
                applicability={"prompt_paths": ["resumed_hitl", "recovery_path"], "thread_id": thread_id},
                source_turn_ids=source_turn_ids,
                source_run_ids=source_run_ids,
                source_memory_ids=source_memory_ids,
            )
        )

    accepted: list[MemoryCandidate] = []
    for candidate in _dedupe_candidates(candidates):
        if _is_forbidden_long_term(candidate):
            continue
        accepted.append(candidate)
    return accepted
