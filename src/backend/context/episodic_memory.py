from __future__ import annotations

from typing import Any

from src.backend.context.models import EpisodicSummary


def _dedupe(items: list[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in items:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def _merge(previous: dict[str, Any] | None, field_name: str, additions: list[str], *, limit: int = 8) -> tuple[str, ...]:
    merged = list(previous.get(field_name, []) if isinstance(previous, dict) else [])
    merged.extend(additions)
    return _dedupe(merged[:limit])


def build_episodic_summary(
    state: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    updated_at: str = "",
) -> EpisodicSummary:
    final_answer = str(state.get("final_answer", "") or "").strip()
    key_facts: list[str] = []
    completed_subtasks: list[str] = []
    rejected_paths: list[str] = []
    important_decisions: list[str] = []
    important_artifacts: list[str] = []
    open_loops: list[str] = []

    if final_answer:
        key_facts.append(final_answer[:220])
        completed_subtasks.append(str(state.get("path_kind", "") or "answered"))

    route_decision = state.get("route_decision")
    if route_decision is not None:
        important_decisions.append(
            f"route={getattr(route_decision, 'intent', '')} source={getattr(route_decision, 'source', '')}"
        )
    skill_decision = state.get("skill_decision")
    if getattr(skill_decision, "use_skill", False):
        important_decisions.append(f"skill={getattr(skill_decision, 'skill_name', '')}")

    approval_decision = str(state.get("approval_decision", "") or "").strip().lower()
    if approval_decision == "reject":
        rejected_paths.append("capability execution rejected by user")
    elif approval_decision == "edit":
        important_decisions.append("capability input was edited before execution")

    recovery_action = str(state.get("recovery_action", "") or "").strip()
    if recovery_action:
        important_decisions.append(f"recovery={recovery_action}")
        if recovery_action in {"fail_fast", "fallback_to_answer"}:
            rejected_paths.append(f"capability path ended via {recovery_action}")

    for item in list(state.get("capability_results", []) or [])[-3:]:
        if not isinstance(item, dict):
            continue
        capability_id = str(item.get("capability_id", "") or "").strip()
        status = str(item.get("status", "") or "").strip()
        if capability_id:
            if status in {"success", "partial"}:
                completed_subtasks.append(f"{capability_id}::{status}")
            elif status == "failed":
                rejected_paths.append(f"{capability_id}::{item.get('error_type', 'failed')}")
            important_artifacts.append(capability_id)

    for item in list(state.get("memory_retrieval", []) or [])[:3]:
        if isinstance(item, dict):
            important_artifacts.append(str(item.get("source", "") or item.get("source_path", "") or ""))

    knowledge_retrieval = state.get("knowledge_retrieval")
    for evidence in list(getattr(knowledge_retrieval, "evidences", []) or [])[:4]:
        important_artifacts.append(str(getattr(evidence, "source_path", "") or ""))

    interrupt_request = state.get("interrupt_request")
    if isinstance(interrupt_request, dict) and interrupt_request:
        open_loops.append(f"pending approval for {interrupt_request.get('capability_id', 'capability')}")
    last_failure = state.get("last_failure")
    if isinstance(last_failure, dict) and last_failure:
        open_loops.append(f"failure pending: {last_failure.get('capability_id', 'capability')}")
    if str(getattr(knowledge_retrieval, "status", "") or "").strip().lower() == "partial":
        open_loops.append("knowledge answer remains partial")

    previous_version = 0
    if isinstance(previous, dict):
        try:
            previous_version = int(previous.get("summary_version", 0) or 0)
        except (TypeError, ValueError):
            previous_version = 0

    thread_id = str(
        state.get("thread_id", "")
        or state.get("session_id", "")
        or state.get("run_id", "")
        or (previous.get("thread_id", "") if isinstance(previous, dict) else "")
    )

    next_key_facts = _merge(previous, "key_facts", key_facts)
    next_completed_subtasks = _merge(previous, "completed_subtasks", completed_subtasks)
    next_rejected_paths = _merge(previous, "rejected_paths", rejected_paths)
    next_important_decisions = _merge(previous, "important_decisions", important_decisions)
    next_important_artifacts = _merge(previous, "important_artifacts", important_artifacts)
    next_open_loops = _merge(previous, "open_loops", open_loops)

    summary_changed = (
        next_key_facts != tuple(previous.get("key_facts", []) if isinstance(previous, dict) else ())
        or next_completed_subtasks != tuple(previous.get("completed_subtasks", []) if isinstance(previous, dict) else ())
        or next_rejected_paths != tuple(previous.get("rejected_paths", []) if isinstance(previous, dict) else ())
        or next_important_decisions != tuple(previous.get("important_decisions", []) if isinstance(previous, dict) else ())
        or next_important_artifacts != tuple(previous.get("important_artifacts", []) if isinstance(previous, dict) else ())
        or next_open_loops != tuple(previous.get("open_loops", []) if isinstance(previous, dict) else ())
    )

    return EpisodicSummary(
        thread_id=thread_id,
        summary_version=max(1, previous_version + (1 if summary_changed or previous_version == 0 else 0)),
        key_facts=next_key_facts,
        completed_subtasks=next_completed_subtasks,
        rejected_paths=next_rejected_paths,
        important_decisions=next_important_decisions,
        important_artifacts=next_important_artifacts,
        open_loops=next_open_loops,
        updated_at=str(updated_at or state.get("checkpoint_meta", {}).get("updated_at", "") or ""),
    )
