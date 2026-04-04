"""Capability governance state and failure taxonomy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.capability_types import CapabilityResult, CapabilitySpec


FAILURE_TAXONOMY: tuple[str, ...] = (
    "approval_required",
    "budget_exhausted",
    "repeated_call_limit",
    "timeout",
    "invalid_input",
    "not_found",
    "path_traversal",
    "network_error",
    "nonzero_exit",
    "blocked_command",
    "capability_unavailable",
    "execution_error",
    "unknown_error",
)


@dataclass(frozen=True)
class CapabilityBudgetPolicy:
    max_budget_cost: int = 8
    max_total_calls: int = 12


@dataclass(frozen=True)
class CapabilityGovernanceDecision:
    allowed: bool
    error_type: str = ""
    error_message: str = ""

    def to_blocked_result(self, *, call_id: str) -> CapabilityResult:
        return CapabilityResult(
            status="blocked",
            payload={},
            partial=False,
            error_type=self.error_type or "unknown_error",
            error_message=self.error_message or "Capability invocation blocked.",
            retryable=False,
            call_id=call_id,
            retry_count=0,
        )


@dataclass
class CapabilityGovernor:
    budget_policy: CapabilityBudgetPolicy = field(default_factory=CapabilityBudgetPolicy)
    total_budget_cost: int = 0
    total_calls: int = 0
    capability_counts: dict[str, int] = field(default_factory=dict)
    failure_counts: dict[str, int] = field(default_factory=dict)

    def check(self, spec: CapabilitySpec) -> CapabilityGovernanceDecision:
        if spec.approval_required:
            return CapabilityGovernanceDecision(
                allowed=False,
                error_type="approval_required",
                error_message=f"{spec.capability_id} is marked as approval_required and cannot run automatically.",
            )
        if self.total_calls >= self.budget_policy.max_total_calls:
            return CapabilityGovernanceDecision(
                allowed=False,
                error_type="budget_exhausted",
                error_message="Per-run capability call budget exhausted.",
            )
        if self.total_budget_cost + spec.budget_cost > self.budget_policy.max_budget_cost:
            return CapabilityGovernanceDecision(
                allowed=False,
                error_type="budget_exhausted",
                error_message="Per-run capability budget cost exhausted.",
            )
        if self.capability_counts.get(spec.capability_id, 0) >= spec.repeated_call_limit:
            return CapabilityGovernanceDecision(
                allowed=False,
                error_type="repeated_call_limit",
                error_message=f"{spec.capability_id} exceeded its repeated_call_limit for this run.",
            )
        return CapabilityGovernanceDecision(allowed=True)

    def record_attempt(self, spec: CapabilitySpec) -> None:
        self.total_calls += 1
        self.total_budget_cost += spec.budget_cost
        self.capability_counts[spec.capability_id] = self.capability_counts.get(spec.capability_id, 0) + 1

    def record_result(self, spec: CapabilitySpec, result: CapabilityResult) -> None:
        if result.status in {"failed", "blocked"} and result.error_type:
            self.failure_counts[result.error_type] = self.failure_counts.get(result.error_type, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_budget_cost": self.budget_policy.max_budget_cost,
            "max_total_calls": self.budget_policy.max_total_calls,
            "total_budget_cost": self.total_budget_cost,
            "total_calls": self.total_calls,
            "capability_counts": dict(self.capability_counts),
            "failure_counts": dict(self.failure_counts),
        }


def is_retryable_error(error_type: str, spec: CapabilitySpec) -> bool:
    return str(error_type or "") in set(spec.retry_policy.retryable_error_types)
