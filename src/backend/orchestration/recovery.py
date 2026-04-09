from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CapabilityFailure:
    failure_key: str
    capability_id: str
    capability_type: str
    call_id: str
    status: str
    error_type: str
    error_message: str
    payload: dict[str, Any]
    input_payload: dict[str, Any]
    display_name: str
    risk_level: str
    approval_required: bool
    retry_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_key": self.failure_key,
            "capability_id": self.capability_id,
            "capability_type": self.capability_type,
            "call_id": self.call_id,
            "status": self.status,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "payload": dict(self.payload),
            "input_payload": dict(self.input_payload),
            "display_name": self.display_name,
            "risk_level": self.risk_level,
            "approval_required": self.approval_required,
            "retry_count": self.retry_count,
        }


def extract_latest_failed_capability(state: dict[str, Any]) -> CapabilityFailure | None:
    for raw_item in reversed(list(state.get("capability_results", []) or [])):
        status = str(raw_item.get("status", "") or "")
        error_type = str(raw_item.get("error_type", "") or "")
        if status != "failed" or error_type not in {"timeout", "network_error", "capability_unavailable", "execution_error"}:
            continue
        capability_id = str(raw_item.get("capability_id", "") or "")
        normalized_input = json.dumps(raw_item.get("input", {}) or {}, ensure_ascii=False, sort_keys=True)
        failure_key = f"{capability_id}:{error_type}:{normalized_input}"
        return CapabilityFailure(
            failure_key=failure_key,
            capability_id=capability_id,
            capability_type=str(raw_item.get("capability_type", "") or ""),
            call_id=str(raw_item.get("call_id", "") or ""),
            status=status,
            error_type=error_type,
            error_message=str(raw_item.get("error_message", "") or ""),
            payload=dict(raw_item.get("payload", {}) or {}),
            input_payload=dict(raw_item.get("input", {}) or {}),
            display_name=str(raw_item.get("display_name", "") or capability_id),
            risk_level=str(raw_item.get("risk_level", "") or ""),
            approval_required=bool(raw_item.get("approval_required", False)),
            retry_count=int(raw_item.get("retry_count", 0) or 0),
        )
    return None


def build_recovery_fallback_answer(*, failure: CapabilityFailure, recovered: bool, fail_fast: bool) -> str:
    if fail_fast:
        return (
            f"I couldn't complete {failure.display_name} because it failed with "
            f"{failure.error_type}: {failure.error_message or 'unknown error'}."
        )
    prefix = "I retried once but it still failed." if recovered else "I could not complete that capability."
    return (
        f"{prefix} {failure.display_name} returned {failure.error_type}: "
        f"{failure.error_message or 'unknown error'}."
    )


def build_recovery_hitl_request(
    *,
    state: dict[str, Any],
    failure: CapabilityFailure,
    checkpoint_id: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "run_id": str(state.get("run_id", "") or ""),
        "thread_id": str(state.get("thread_id", "") or ""),
        "session_id": state.get("session_id"),
        "checkpoint_id": checkpoint_id,
        "capability_id": failure.capability_id,
        "capability_type": failure.capability_type,
        "display_name": failure.display_name,
        "risk_level": failure.risk_level or "medium",
        "reason": reason,
        "proposed_input": dict(failure.input_payload),
    }
