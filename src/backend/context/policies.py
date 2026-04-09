from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.backend.context.models import ContextPathKind


_PREFERENCE_PATTERNS = (
    re.compile(r"\b(prefer|always|never|only|must|avoid|don't|do not|keep)\b", re.IGNORECASE),
    re.compile(r"(偏好|总是|不要|只用|必须|避免|禁用)"),
)
_ABSOLUTE_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_EXTERNAL_REFERENCE_HINT = re.compile(r"\b(linear|slack|grafana|dashboard|runbook|notion|jira|confluence)\b", re.IGNORECASE)
_ARTIFACT_PATH_HINT = re.compile(r"\b[\w./-]+\.(pdf|docx|xlsx|csv|json|md)\b", re.IGNORECASE)
_REFERENTIAL_QUERY_HINT = re.compile(r"\b(earlier|before|previous|last time|that error|that result|what did we|remind me)\b", re.IGNORECASE)
_ROLE_HINT = re.compile(
    r"\b(i am|i'm|i work as|my role is|i've been writing|first time touching|first time using)\b",
    re.IGNORECASE,
)
_PROJECT_FACT_HINT = re.compile(
    r"\b(freeze|deadline|release|migration|incident|policy|owner|stakeholder|due|roadmap|why we're|why we are)\b",
    re.IGNORECASE,
)


def project_namespace(base_dir: Path | None) -> str:
    return f"project:{(base_dir.name if base_dir is not None else 'default').lower()}"


def user_namespace(user_id: str | None = None) -> str:
    return f"user:{(user_id or 'default').strip() or 'default'}"


def thread_namespace(thread_id: str) -> str:
    return f"thread:{thread_id}"


def fingerprint_for(kind: str, namespace: str, content: str, tags: list[str] | tuple[str, ...]) -> str:
    payload = f"{kind}|{namespace}|{content.strip()}|{','.join(sorted(str(item) for item in tags))}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def conflict_key_for(memory_type: str, namespace: str, title: str) -> str:
    normalized_title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    payload = f"{memory_type}|{namespace}|{normalized_title}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def semantic_query_for(state: dict[str, Any], working_memory: dict[str, Any]) -> str:
    parts = [
        str(working_memory.get("current_goal", "") or ""),
        str(working_memory.get("latest_user_intent", "") or ""),
        " ".join(str(item) for item in working_memory.get("active_entities", []) or []),
        " ".join(str(item) for item in working_memory.get("active_artifacts", []) or []),
        " ".join(str(item) for item in working_memory.get("unresolved_items", []) or []),
        str(state.get("user_message", "") or ""),
    ]
    return " ".join(part for part in parts if part).strip()


def procedural_query_for(state: dict[str, Any], working_memory: dict[str, Any]) -> str:
    parts = [
        str(working_memory.get("latest_user_intent", "") or ""),
        " ".join(str(item) for item in working_memory.get("active_constraints", []) or []),
        " ".join(str(item) for item in state.get("selected_capabilities", []) or []),
        " ".join(str(item) for item in working_memory.get("latest_capability_results", []) or []),
    ]
    return " ".join(part for part in parts if part).strip()


def conversation_query_for(state: dict[str, Any], working_memory: dict[str, Any]) -> str:
    parts = [
        str(state.get("user_message", "") or ""),
        str(working_memory.get("latest_user_intent", "") or ""),
        " ".join(str(item) for item in working_memory.get("unresolved_items", []) or []),
    ]
    return " ".join(part for part in parts if part).strip()


def should_use_conversation_recall(path_kind: ContextPathKind, state: dict[str, Any], *, history_trimmed: bool) -> bool:
    if path_kind in {"resumed_hitl", "recovery_path"}:
        return True
    query = str(state.get("user_message", "") or "").strip()
    if history_trimmed and query:
        return True
    return bool(_REFERENTIAL_QUERY_HINT.search(query))


def freshness_state(updated_at: str, stale_after: str) -> str:
    if not updated_at or not stale_after:
        return "fresh"
    try:
        updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        stale_dt = datetime.fromisoformat(stale_after.replace("Z", "+00:00"))
    except ValueError:
        return "fresh"
    now = datetime.now(timezone.utc)
    if now >= stale_dt:
        return "stale"
    if now >= updated_dt + (stale_dt - updated_dt) / 2:
        return "aging"
    return "fresh"


def stale_after_from(updated_at: str, *, days: int) -> str:
    if not updated_at:
        return ""
    try:
        updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (updated_dt + timedelta(days=max(0, days))).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def infer_reference_tags(text: str) -> tuple[str, ...]:
    tags: list[str] = []
    if _URL_PATTERN.search(text):
        tags.append("url")
    if _EXTERNAL_REFERENCE_HINT.search(text):
        tags.append("external")
    if _ARTIFACT_PATH_HINT.search(text):
        tags.append("artifact")
    if _ABSOLUTE_DATE.search(text):
        tags.append("dated")
    return tuple(dict.fromkeys(tags))


def looks_like_user_profile(text: str) -> bool:
    return bool(_ROLE_HINT.search(text))


def looks_like_feedback(text: str) -> bool:
    return any(pattern.search(text) for pattern in _PREFERENCE_PATTERNS)


def looks_like_project_fact(text: str) -> bool:
    return bool(_PROJECT_FACT_HINT.search(text) or _ABSOLUTE_DATE.search(text))


def looks_like_external_reference(text: str) -> bool:
    return bool(_URL_PATTERN.search(text) or _EXTERNAL_REFERENCE_HINT.search(text))


def looks_like_artifact_map(text: str) -> bool:
    return bool(_ARTIFACT_PATH_HINT.search(text))
