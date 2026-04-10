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
    r"\b(timeout|temporary|transient|retrying|failed once|one-off|flaky|just this once)\b",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_REPO_DERIVABLE_PATTERN = re.compile(
    r"\b(repo|repository|codebase|file structure|folder structure|architecture|git history|commit|branch|src/|backend/|frontend/|function|class)\b",
    re.IGNORECASE,
)
_EPHEMERAL_DETAIL_PATTERN = re.compile(
    r"\b(this run|this turn|just now|for this prompt only|temporary workaround|current failure scene)\b",
    re.IGNORECASE,
)


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
    body_fields: tuple[str, ...]
    require_why: bool
    require_how_to_apply: bool
    confidence_lower_bound: float


RULES: dict[MemoryType, MemoryGovernanceRule] = {
    "user_profile": MemoryGovernanceRule(
        "user_profile", "semantic", "user", 180, 90, True, False, True, 1, "User profile",
        ("profile", "why"), True, False, 0.7
    ),
    "preference_feedback": MemoryGovernanceRule(
        "preference_feedback", "procedural", "user", 90, 100, True, False, True, 1, "Preference",
        ("preference", "why", "how_to_apply"), True, True, 0.7
    ),
    "project_fact": MemoryGovernanceRule(
        "project_fact", "semantic", "project", 30, 80, True, False, True, 1, "Project fact",
        ("fact", "why", "validation_hint"), True, False, 0.75
    ),
    "external_reference": MemoryGovernanceRule(
        "external_reference", "semantic", "project", 120, 70, True, False, True, 1, "External reference",
        ("reference", "why", "how_to_apply"), True, False, 0.72
    ),
    "workflow_rule": MemoryGovernanceRule(
        "workflow_rule", "procedural", "project", 120, 95, True, False, True, 1, "Workflow rule",
        ("rule", "why", "how_to_apply"), True, True, 0.72
    ),
    "capability_lesson": MemoryGovernanceRule(
        "capability_lesson", "procedural", "project", 45, 75, False, False, False, 2, "Capability lesson",
        ("lesson", "trigger", "why", "how_to_apply"), True, True, 0.68
    ),
    "artifact_map": MemoryGovernanceRule(
        "artifact_map", "semantic", "project", 60, 60, False, False, False, 2, "Artifact map",
        ("artifact", "mapping", "why", "how_to_apply"), True, True, 0.68
    ),
    "session_episode": MemoryGovernanceRule(
        "session_episode", "episodic", "thread", 5, 20, False, True, True, 1, "Session episode",
        ("episode", "why"), True, False, 0.55
    ),
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


def _line_body(memory_type: MemoryType, text: str) -> dict[str, Any]:
    normalized = _sanitize_text(text, limit=320)
    if memory_type == "user_profile":
        return {
            "profile": normalized,
            "why": "The user explicitly described their role, experience, or background in a way that should shape future responses.",
        }
    if memory_type == "preference_feedback":
        return {
            "preference": normalized,
            "why": "The user explicitly stated a durable response preference or feedback signal.",
            "how_to_apply": "Bias future answers toward this style unless newer feedback supersedes it.",
        }
    if memory_type == "project_fact":
        return {
            "fact": normalized,
            "why": "This is a stable project fact with cross-session value and should survive beyond the current turn.",
            "validation_hint": "Re-check against current evidence if the fact is date-sensitive or operationally important.",
        }
    if memory_type == "external_reference":
        return {
            "reference": normalized,
            "why": "This external link or document was cited as a durable reference point for future work.",
            "how_to_apply": "Use it as a retrieval or navigation hint when the same project topic reappears.",
        }
    if memory_type == "workflow_rule":
        return {
            "rule": normalized,
            "why": "This rule captures a durable execution preference, constraint, or policy.",
            "how_to_apply": "Apply it before selecting tools, drafting answers, or resuming interrupted work.",
        }
    if memory_type == "capability_lesson":
        return {
            "lesson": normalized,
            "trigger": "Repeat capability failure or recovery pattern",
            "why": "This lesson captures a non-trivial capability behavior worth reusing later.",
            "how_to_apply": "Use it as a retrieval candidate when similar tool failures or recovery flows recur.",
        }
    if memory_type == "artifact_map":
        return {
            "artifact": normalized,
            "mapping": normalized,
            "why": "This artifact reference helps map durable project assets to their meaning or usage.",
            "how_to_apply": "Use it to locate the right asset quickly instead of re-discovering it from scratch.",
        }
    return {
        "episode": normalized,
        "why": "This is a concise session episode summary that can later be consolidated into stable memory.",
    }


def _render_body_content(memory_type: MemoryType, body: dict[str, Any]) -> tuple[str, str]:
    if memory_type == "user_profile":
        content = f"profile: {body.get('profile', '')}\nwhy: {body.get('why', '')}"
        summary = str(body.get("profile", "") or "")
    elif memory_type == "preference_feedback":
        content = (
            f"preference: {body.get('preference', '')}\n"
            f"why: {body.get('why', '')}\n"
            f"how_to_apply: {body.get('how_to_apply', '')}"
        )
        summary = str(body.get("preference", "") or "")
    elif memory_type == "project_fact":
        content = (
            f"fact: {body.get('fact', '')}\n"
            f"why: {body.get('why', '')}\n"
            f"validation_hint: {body.get('validation_hint', '')}"
        )
        summary = str(body.get("fact", "") or "")
    elif memory_type == "external_reference":
        content = (
            f"reference: {body.get('reference', '')}\n"
            f"why: {body.get('why', '')}\n"
            f"how_to_apply: {body.get('how_to_apply', '')}"
        )
        summary = str(body.get("reference", "") or "")
    elif memory_type == "workflow_rule":
        content = (
            f"rule: {body.get('rule', '')}\n"
            f"why: {body.get('why', '')}\n"
            f"how_to_apply: {body.get('how_to_apply', '')}"
        )
        summary = str(body.get("rule", "") or "")
    elif memory_type == "capability_lesson":
        content = (
            f"lesson: {body.get('lesson', '')}\n"
            f"trigger: {body.get('trigger', '')}\n"
            f"why: {body.get('why', '')}\n"
            f"how_to_apply: {body.get('how_to_apply', '')}"
        )
        summary = str(body.get("lesson", "") or "")
    elif memory_type == "artifact_map":
        content = (
            f"artifact: {body.get('artifact', '')}\n"
            f"mapping: {body.get('mapping', '')}\n"
            f"why: {body.get('why', '')}\n"
            f"how_to_apply: {body.get('how_to_apply', '')}"
        )
        summary = str(body.get("mapping", "") or body.get("artifact", "") or "")
    else:
        content = f"episode: {body.get('episode', '')}\nwhy: {body.get('why', '')}"
        summary = str(body.get("episode", "") or "")
    return _sanitize_text(content, limit=1200 if memory_type == "session_episode" else 640), _sanitize_text(summary, limit=220)


def _candidate(
    *,
    memory_type: MemoryType,
    base_dir: Path | None,
    thread_id: str,
    title: str,
    body: dict[str, Any],
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
    content_value, summary_value = _render_body_content(memory_type, body)
    fingerprint = fingerprint_for(rule.kind, namespace, content_value, tags)
    return MemoryCandidate(
        kind=rule.kind,
        memory_type=memory_type,
        scope=rule.scope,
        namespace=namespace,
        title=title,
        content=content_value,
        summary=summary_value,
        body=dict(body),
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


def _validate_contract(candidate: MemoryCandidate) -> bool:
    rule = rule_for(candidate.memory_type)
    if candidate.confidence < rule.confidence_lower_bound:
        return False
    if not candidate.body:
        return False
    for field_name in rule.body_fields:
        value = candidate.body.get(field_name)
        if value in (None, "", [], ()):
            return False
    if rule.require_why and not str(candidate.body.get("why", "") or "").strip():
        return False
    if rule.require_how_to_apply and not str(candidate.body.get("how_to_apply", "") or "").strip():
        return False
    return True


def _is_forbidden_long_term(candidate: MemoryCandidate) -> bool:
    if candidate.memory_type == "session_episode":
        return False
    rule = rule_for(candidate.memory_type)
    if candidate.confidence < rule.confidence_lower_bound:
        return True
    source = str(candidate.source or "").strip().lower()
    content = f"{candidate.title}\n{candidate.content}\n{candidate.summary}".strip()
    metadata = dict(candidate.metadata)
    if metadata.get("derivable_from_repo") or _REPO_DERIVABLE_PATTERN.search(content):
        return True
    if metadata.get("raw_tool_output") or metadata.get("raw_trace") or metadata.get("raw_checkpoint") or metadata.get("raw_hitl"):
        return True
    if source in {"raw_trace", "raw_checkpoint", "raw_hitl", "tool_output"}:
        return True
    if metadata.get("low_confidence_transient") or metadata.get("no_cross_session_value"):
        return True
    if _NOISY_OUTPUT_PATTERN.search(content):
        return True
    if _EPHEMERAL_DETAIL_PATTERN.search(content):
        return True
    if metadata.get("transient_failure") and candidate.memory_type not in {"capability_lesson"}:
        return True
    if _TEMPORARY_FAILURE_PATTERN.search(content) and candidate.memory_type in {"project_fact", "artifact_map", "workflow_rule"}:
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
                    body=_line_body("user_profile", line),
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
                    body=_line_body("preference_feedback", line),
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
                    body=_line_body("project_fact", line),
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
                    body=_line_body("external_reference", line),
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
                    body=_line_body("artifact_map", line),
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
                memory_type="workflow_rule",
                base_dir=base_dir,
                thread_id=thread_id,
                title="Workflow rule",
                body=_line_body("workflow_rule", text),
                tags=("workflow", "rule"),
                source="working_memory",
                updated_at=updated_at,
                confidence=0.76,
                applicability={"prompt_paths": ["capability_path", "resumed_hitl", "recovery_path"]},
                source_turn_ids=source_turn_ids,
                source_run_ids=source_run_ids,
                source_memory_ids=source_memory_ids,
            )
        )

    for decision in list(episodic_summary.important_decisions)[:3]:
        text = str(decision).strip()
        if not text:
            continue
        if text.startswith("route=") or text.startswith("skill="):
            continue
        candidates.append(
            _candidate(
                memory_type="workflow_rule",
                base_dir=base_dir,
                thread_id=thread_id,
                title="Workflow rule",
                body=_line_body("workflow_rule", text),
                tags=("decision", "workflow"),
                source="episodic_summary",
                updated_at=updated_at,
                confidence=0.72,
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
        lesson_body = _line_body("capability_lesson", lesson)
        lesson_body["trigger"] = str(last_failure.get("capability_id", "capability") or "capability failure")
        candidates.append(
            _candidate(
                memory_type="capability_lesson",
                base_dir=base_dir,
                thread_id=thread_id,
                title="Capability lesson",
                body=lesson_body,
                tags=("capability", "failure", "lesson"),
                metadata={"transient_failure": False},
                source="failure_state",
                updated_at=updated_at,
                confidence=0.7,
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
                    body=_line_body("external_reference", text),
                    tags=("reference", "url"),
                    source="episodic_summary",
                    updated_at=updated_at,
                    confidence=0.74,
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
                    body=_line_body("artifact_map", text),
                    tags=("artifact", "map"),
                    metadata={"derivable_from_repo": False},
                    source="episodic_summary",
                    updated_at=updated_at,
                    confidence=0.7,
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
                "body": candidate.body,
                "namespace": candidate.namespace,
                "fingerprint": candidate.fingerprint,
                "conflict_key": candidate.conflict_key,
                "confidence": candidate.confidence,
                "direct_prompt": candidate.direct_prompt,
            }
            for candidate in candidates
            if candidate.memory_type != "session_episode"
        ]
        episode_body = {
            "episode": " | ".join(episode_lines),
            "why": "This captures the current session episode so repeated patterns can later be consolidated into stable memory.",
            "stable_candidates": stable_hints,
        }
        candidates.append(
            _candidate(
                memory_type="session_episode",
                base_dir=base_dir,
                thread_id=thread_id,
                title="Session episode",
                body=episode_body,
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
        if not _validate_contract(candidate):
            continue
        if _is_forbidden_long_term(candidate):
            continue
        accepted.append(candidate)
    return accepted
