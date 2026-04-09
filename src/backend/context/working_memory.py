from __future__ import annotations

import re
from typing import Any

from src.backend.context.models import WorkingMemory


_ENTITY_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9_.:/-]{1,40}\b")


def _dedupe(items: list[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in items:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def _extract_entities(state: dict[str, Any]) -> tuple[str, ...]:
    entities: list[str] = []
    message = str(state.get("user_message", "") or "")
    entities.extend(match.group(0) for match in _ENTITY_PATTERN.finditer(message))
    route_decision = state.get("route_decision")
    subtype = str(getattr(route_decision, "subtype", "") or "")
    if subtype:
        entities.append(subtype)
    knowledge_retrieval = state.get("knowledge_retrieval")
    entities.extend(str(item).strip() for item in getattr(knowledge_retrieval, "entity_hints", []) or [])
    return _dedupe(entities[:8])


def _active_constraints(state: dict[str, Any]) -> tuple[str, ...]:
    constraints: list[str] = []
    execution_strategy = state.get("execution_strategy")
    if execution_strategy is not None:
        constraints.extend(str(item) for item in execution_strategy.to_instructions())
    approval_decision = str(state.get("approval_decision", "") or "").strip().lower()
    if approval_decision:
        constraints.append(f"approval_decision={approval_decision}")
    recovery_action = str(state.get("recovery_action", "") or "").strip()
    if recovery_action:
        constraints.append(f"recovery_action={recovery_action}")
    return _dedupe(constraints[:8])


def _active_artifacts(state: dict[str, Any]) -> tuple[str, ...]:
    artifacts: list[str] = []
    for item in list(state.get("memory_retrieval", []) or []):
        if isinstance(item, dict):
            artifacts.append(str(item.get("source", "") or item.get("source_path", "") or ""))
    knowledge_retrieval = state.get("knowledge_retrieval")
    for evidence in list(getattr(knowledge_retrieval, "evidences", []) or []):
        artifacts.append(str(getattr(evidence, "source_path", "") or ""))
    for item in list(state.get("capability_results", []) or []):
        if isinstance(item, dict):
            artifacts.append(str(item.get("capability_id", "") or ""))
    interrupt_request = state.get("interrupt_request")
    if isinstance(interrupt_request, dict):
        artifacts.append(str(interrupt_request.get("capability_id", "") or ""))
    return _dedupe([item for item in artifacts if item][:10])


def _latest_capability_results(state: dict[str, Any]) -> tuple[str, ...]:
    summaries: list[str] = []
    for item in list(state.get("capability_results", []) or [])[-3:]:
        if not isinstance(item, dict):
            continue
        capability_id = str(item.get("capability_id", "") or "capability")
        status = str(item.get("status", "") or "")
        error_type = str(item.get("error_type", "") or "")
        payload = item.get("payload", {})
        payload_text = str(payload or "").strip()
        if len(payload_text) > 160:
            payload_text = payload_text[:160].rstrip() + " ..."
        summary = f"{capability_id} [{status}]"
        if error_type:
            summary += f" error={error_type}"
        if payload_text and status in {"success", "partial"}:
            summary += f" :: {payload_text}"
        summaries.append(summary)
    return _dedupe(summaries)


def _latest_retrieval_summary(state: dict[str, Any]) -> str:
    memory_hits = len(list(state.get("memory_retrieval", []) or []))
    knowledge_retrieval = state.get("knowledge_retrieval")
    if knowledge_retrieval is not None:
        evidences = len(list(getattr(knowledge_retrieval, "evidences", []) or []))
        status = str(getattr(knowledge_retrieval, "status", "") or "success")
        reason = str(getattr(knowledge_retrieval, "reason", "") or "").strip()
        summary = f"knowledge evidences={evidences} status={status}"
        if reason:
            summary += f" reason={reason}"
        if memory_hits:
            summary += f" | memory_hits={memory_hits}"
        return summary
    if memory_hits:
        return f"memory_hits={memory_hits}"
    return ""


def _unresolved_items(state: dict[str, Any]) -> tuple[str, ...]:
    items: list[str] = []
    interrupt_request = state.get("interrupt_request")
    if isinstance(interrupt_request, dict) and interrupt_request:
        items.append(f"approval pending for {interrupt_request.get('capability_id', 'capability')}")
    last_failure = state.get("last_failure")
    if isinstance(last_failure, dict) and last_failure:
        items.append(
            f"failure {last_failure.get('capability_id', 'capability')}::{last_failure.get('error_type', 'unknown')}"
        )
    recovery_action = str(state.get("recovery_action", "") or "").strip()
    if recovery_action in {"retry_once", "escalate_to_hitl"}:
        items.append(f"recovery pending via {recovery_action}")
    knowledge_retrieval = state.get("knowledge_retrieval")
    if str(getattr(knowledge_retrieval, "status", "") or "").strip().lower() == "partial":
        items.append("knowledge evidence is partial")
    if str(state.get("approval_decision", "") or "").strip().lower() == "reject":
        items.append("approval rejected; capability skipped")
    return _dedupe(items[:8])


def build_working_memory(state: dict[str, Any], *, updated_at: str = "") -> WorkingMemory:
    route_decision = state.get("route_decision")
    latest_user_intent = str(getattr(route_decision, "intent", "") or state.get("path_kind", "") or "")
    current_goal = str(state.get("user_message", "") or "").strip()
    return WorkingMemory(
        thread_id=str(state.get("thread_id", "") or state.get("session_id", "") or state.get("run_id", "") or ""),
        current_goal=current_goal,
        active_constraints=_active_constraints(state),
        active_entities=_extract_entities(state),
        active_artifacts=_active_artifacts(state),
        latest_capability_results=_latest_capability_results(state),
        latest_retrieval_summary=_latest_retrieval_summary(state),
        latest_user_intent=latest_user_intent,
        unresolved_items=_unresolved_items(state),
        updated_at=str(updated_at or state.get("checkpoint_meta", {}).get("updated_at", "") or ""),
    )
