from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.backend.context.models import ContextPathKind
from src.backend.runtime.token_utils import count_tokens


_PREFERENCE_PATTERNS = (
    re.compile(r"\b(prefer|always|never|only|must|avoid|don't|do not|keep)\b", re.IGNORECASE),
    re.compile(r"(鍋忓ソ|鎬绘槸|涓嶈|鍙敤|蹇呴』|閬垮厤|绂佺敤)"),
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


@dataclass(frozen=True)
class SessionMemoryGatePolicy:
    initial_token_threshold: int = 16
    token_growth_threshold: int = 96
    natural_pause_token_threshold: int = 48
    checkpoint_token_threshold: int = 40
    capability_call_threshold: int = 2
    minimum_turn_growth: int = 2


@dataclass(frozen=True)
class SessionMemoryUpdateDecision:
    should_update: bool
    trigger: str
    reason: str
    forced: bool = False
    stats: dict[str, Any] = field(default_factory=dict)
    next_state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AutoDreamLifecyclePolicy:
    enabled: bool
    min_minutes_between_runs: int
    scan_throttle_seconds: int
    min_recent_episodes: int
    min_stable_signal_count: int


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


def session_memory_gate_policy() -> SessionMemoryGatePolicy:
    return SessionMemoryGatePolicy()


def autodream_policy() -> AutoDreamLifecyclePolicy:
    enabled = str(os.getenv("RAGCLAW_AUTODREAM_ENABLED", "1") or "1").strip().lower() not in {"0", "false", "off"}
    return AutoDreamLifecyclePolicy(
        enabled=enabled,
        min_minutes_between_runs=int(str(os.getenv("RAGCLAW_AUTODREAM_MIN_MINUTES", "20") or "20")),
        scan_throttle_seconds=int(str(os.getenv("RAGCLAW_AUTODREAM_SCAN_THROTTLE", "90") or "90")),
        min_recent_episodes=int(str(os.getenv("RAGCLAW_AUTODREAM_MIN_EPISODES", "2") or "2")),
        min_stable_signal_count=int(str(os.getenv("RAGCLAW_AUTODREAM_MIN_SIGNALS", "2") or "2")),
    )


def session_signal_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    history = list(state.get("history", []) or [])
    capability_results = list(state.get("capability_results", []) or [])
    fragments = [str(state.get("user_message", "") or ""), str(state.get("final_answer", "") or "")]
    for item in history[-10:]:
        if not isinstance(item, dict):
            continue
        fragments.append(str(item.get("content", "") or ""))
    fragments.extend(str(item.get("capability_id", "") or "") for item in capability_results[-6:] if isinstance(item, dict))
    signal_text = "\n".join(fragment for fragment in fragments if fragment.strip())
    checkpoint_meta = dict(state.get("checkpoint_meta", {}) or {})
    return {
        "history_tokens": count_tokens(signal_text),
        "history_count": len(history) + (1 if str(state.get("user_message", "") or "").strip() else 0),
        "capability_count": len(capability_results),
        "natural_pause": bool(str(state.get("final_answer", "") or "").strip()) and not state.get("interrupt_request"),
        "checkpoint_event": bool(str(checkpoint_meta.get("checkpoint_id", "") or "").strip())
        or str(checkpoint_meta.get("run_status", "") or "").strip().lower() in {"resumed", "restoring", "interrupted"},
        "hitl_decision": str(state.get("approval_decision", "") or "").strip().lower(),
        "recovery_action": str(state.get("recovery_action", "") or "").strip(),
        "turn_id": str(state.get("turn_id", "") or "").strip(),
        "run_id": str(state.get("run_id", "") or "").strip(),
        "checkpoint_id": str(checkpoint_meta.get("checkpoint_id", "") or "").strip(),
    }


def decide_session_memory_update(
    state: dict[str, Any],
    *,
    previous_state: dict[str, Any] | None,
    updated_at: str,
) -> SessionMemoryUpdateDecision:
    policy = session_memory_gate_policy()
    previous = dict(previous_state or {})
    signal = session_signal_snapshot(state)
    last_tokens = int(previous.get("last_history_tokens", 0) or 0)
    last_capability_count = int(previous.get("last_capability_count", 0) or 0)
    last_history_count = int(previous.get("last_history_count", 0) or 0)
    update_count = int(previous.get("update_count", 0) or 0)
    token_delta = max(0, int(signal["history_tokens"]) - last_tokens)
    capability_delta = max(0, int(signal["capability_count"]) - last_capability_count)
    history_delta = max(0, int(signal["history_count"]) - last_history_count)
    forced_reason = ""
    if signal["hitl_decision"] in {"approve", "reject", "edit"}:
        forced_reason = f"hitl_{signal['hitl_decision']}"
    elif signal["recovery_action"]:
        forced_reason = "recovery_complete"
    elif signal["checkpoint_event"] and (token_delta >= policy.checkpoint_token_threshold or history_delta >= policy.minimum_turn_growth):
        forced_reason = "checkpoint_low_frequency"

    should_update = False
    reason = ""
    trigger = "skip"

    if forced_reason:
        should_update = True
        reason = forced_reason
        trigger = forced_reason
    elif update_count == 0:
        if int(signal["history_tokens"]) >= policy.initial_token_threshold and (signal["natural_pause"] or capability_delta >= 1):
            should_update = True
            reason = "initial_session_memory"
            trigger = "initial"
        else:
            reason = "below_initial_session_threshold"
    elif token_delta >= policy.token_growth_threshold and capability_delta >= policy.capability_call_threshold:
        should_update = True
        reason = "token_and_capability_threshold"
        trigger = "threshold"
    elif signal["natural_pause"] and token_delta >= policy.natural_pause_token_threshold:
        should_update = True
        reason = "natural_pause"
        trigger = "turn_end"
    else:
        if token_delta < policy.natural_pause_token_threshold:
            reason = "token_growth_below_threshold"
        elif capability_delta < policy.capability_call_threshold and not signal["natural_pause"]:
            reason = "capability_threshold_not_met"
        else:
            reason = "no_stable_pause"

    next_state = dict(previous)
    next_state.update(
        {
            "last_evaluated_at": updated_at,
            "last_decision": "update" if should_update else "skip",
            "last_skip_reason": "" if should_update else reason,
            "last_observed_tokens": int(signal["history_tokens"]),
            "last_observed_capability_count": int(signal["capability_count"]),
            "last_observed_history_count": int(signal["history_count"]),
            "last_turn_id": str(signal["turn_id"] or ""),
            "last_run_id": str(signal["run_id"] or ""),
            "last_checkpoint_id": str(signal["checkpoint_id"] or ""),
        }
    )
    if should_update:
        next_state.update(
            {
                "last_updated_at": updated_at,
                "last_update_reason": reason,
                "last_update_trigger": trigger,
                "last_history_tokens": int(signal["history_tokens"]),
                "last_capability_count": int(signal["capability_count"]),
                "last_history_count": int(signal["history_count"]),
                "update_count": update_count + 1,
            }
        )

    return SessionMemoryUpdateDecision(
        should_update=should_update,
        trigger=trigger,
        reason=reason,
        forced=bool(forced_reason),
        stats={
            "history_tokens": int(signal["history_tokens"]),
            "token_delta": token_delta,
            "capability_count": int(signal["capability_count"]),
            "capability_delta": capability_delta,
            "history_count": int(signal["history_count"]),
            "history_delta": history_delta,
            "natural_pause": bool(signal["natural_pause"]),
            "checkpoint_event": bool(signal["checkpoint_event"]),
            "hitl_decision": str(signal["hitl_decision"] or ""),
            "recovery_action": str(signal["recovery_action"] or ""),
        },
        next_state=next_state,
    )
