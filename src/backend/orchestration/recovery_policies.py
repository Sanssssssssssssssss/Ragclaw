from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.backend.capabilities.types import CapabilitySpec


RecoveryAction = Literal["retry_once", "fallback_to_answer", "escalate_to_hitl", "fail_fast"]

_RECOVERY_ACTION_OVERRIDES: dict[tuple[str, str], RecoveryAction] = {}
_RECOVERY_TYPE_OVERRIDES: dict[tuple[str, str], RecoveryAction] = {}


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    error_type: str
    reason: str


def select_recovery_action(
    *,
    spec: CapabilitySpec | None,
    error_type: str,
    retry_count: int,
    already_escalated: bool,
) -> RecoveryDecision:
    normalized_error = str(error_type or "").strip() or "unknown_error"
    capability_id = str(spec.capability_id if spec is not None else "").strip()
    capability_type = str(spec.capability_type if spec is not None else "").strip()

    override = _RECOVERY_ACTION_OVERRIDES.get((capability_id, normalized_error))
    if override is None:
        override = _RECOVERY_TYPE_OVERRIDES.get((capability_type, normalized_error))
    if override is not None:
        return RecoveryDecision(
            action=override,
            error_type=normalized_error,
            reason=f"Capability-specific recovery override selected {override}.",
        )

    if normalized_error == "timeout":
        if retry_count < 1:
            return RecoveryDecision("retry_once", normalized_error, "Timeout gets one graph-level retry.")
        return RecoveryDecision("fallback_to_answer", normalized_error, "Timeout already retried once; falling back to an answer.")

    if normalized_error == "network_error":
        if retry_count < 1:
            return RecoveryDecision("retry_once", normalized_error, "Network errors get one graph-level retry.")
        return RecoveryDecision("fallback_to_answer", normalized_error, "Network error already retried once; falling back to an answer.")

    if normalized_error == "capability_unavailable":
        if spec is not None and (spec.approval_required or spec.risk_level == "high") and not already_escalated:
            return RecoveryDecision("escalate_to_hitl", normalized_error, "High-risk or approval-sensitive unavailable capability escalates to HITL.")
        return RecoveryDecision("fallback_to_answer", normalized_error, "Capability is unavailable; falling back to an answer.")

    if normalized_error == "execution_error":
        return RecoveryDecision("fail_fast", normalized_error, "Execution errors fail fast unless explicitly overridden.")

    return RecoveryDecision("fail_fast", normalized_error, "Unhandled capability failure type defaults to fail-fast.")
