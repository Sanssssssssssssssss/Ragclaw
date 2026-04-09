from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.capabilities.types import CapabilityRetryPolicy, CapabilitySpec
from src.backend.orchestration.recovery_policies import select_recovery_action


def _spec(*, capability_id: str = "capability", risk_level: str = "low", approval_required: bool = False) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        capability_type="mcp_service",
        display_name="Capability",
        description="test capability",
        when_to_use="use for tests",
        when_not_to_use="never in production",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        error_schema={"type": "object"},
        risk_level=risk_level,  # type: ignore[arg-type]
        timeout_seconds=5,
        retry_policy=CapabilityRetryPolicy(max_retries=0),
        approval_required=approval_required,
        tags=("test",),
    )


class RecoveryPolicyTests(unittest.TestCase):
    def test_timeout_retries_once_then_falls_back(self) -> None:
        spec = _spec()
        first = select_recovery_action(spec=spec, error_type="timeout", retry_count=0, already_escalated=False)
        second = select_recovery_action(spec=spec, error_type="timeout", retry_count=1, already_escalated=False)
        self.assertEqual(first.action, "retry_once")
        self.assertEqual(second.action, "fallback_to_answer")

    def test_network_error_retries_once_then_falls_back(self) -> None:
        spec = _spec()
        first = select_recovery_action(spec=spec, error_type="network_error", retry_count=0, already_escalated=False)
        second = select_recovery_action(spec=spec, error_type="network_error", retry_count=1, already_escalated=False)
        self.assertEqual(first.action, "retry_once")
        self.assertEqual(second.action, "fallback_to_answer")

    def test_capability_unavailable_falls_back_for_low_risk_capabilities(self) -> None:
        spec = _spec(risk_level="low", approval_required=False)
        decision = select_recovery_action(
            spec=spec,
            error_type="capability_unavailable",
            retry_count=0,
            already_escalated=False,
        )
        self.assertEqual(decision.action, "fallback_to_answer")

    def test_capability_unavailable_escalates_for_high_risk_or_approval_required_capabilities(self) -> None:
        high_risk = _spec(capability_id="python_repl", risk_level="high", approval_required=True)
        first = select_recovery_action(
            spec=high_risk,
            error_type="capability_unavailable",
            retry_count=0,
            already_escalated=False,
        )
        second = select_recovery_action(
            spec=high_risk,
            error_type="capability_unavailable",
            retry_count=0,
            already_escalated=True,
        )
        self.assertEqual(first.action, "escalate_to_hitl")
        self.assertEqual(second.action, "fallback_to_answer")

    def test_execution_error_defaults_to_fail_fast(self) -> None:
        spec = _spec()
        decision = select_recovery_action(spec=spec, error_type="execution_error", retry_count=0, already_escalated=False)
        self.assertEqual(decision.action, "fail_fast")


if __name__ == "__main__":
    unittest.main()
