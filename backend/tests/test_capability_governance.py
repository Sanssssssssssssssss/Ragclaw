from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.backend.capabilities.governance import CapabilityBudgetPolicy, CapabilityGovernor
from src.backend.capabilities.types import CapabilityRetryPolicy, CapabilitySpec


def _spec(**overrides):
    base = {
        "capability_id": "terminal",
        "capability_type": "tool",
        "display_name": "Terminal",
        "description": "tool",
        "when_to_use": "inspect workspace",
        "when_not_to_use": "read one file",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "error_schema": {"type": "object"},
        "risk_level": "high",
        "timeout_seconds": 30,
        "retry_policy": CapabilityRetryPolicy(max_retries=0),
        "approval_required": False,
    }
    base.update(overrides)
    return CapabilitySpec(**base)


class CapabilityGovernanceTests(unittest.TestCase):
    def test_repeated_call_limit_is_enforced(self) -> None:
        governor = CapabilityGovernor(CapabilityBudgetPolicy(max_budget_cost=10, max_total_calls=10))
        spec = _spec(repeated_call_limit=1)
        self.assertTrue(governor.check(spec).allowed)
        governor.record_attempt(spec)
        self.assertFalse(governor.check(spec).allowed)
        self.assertEqual(governor.check(spec).error_type, "repeated_call_limit")

    def test_budget_limit_is_enforced(self) -> None:
        governor = CapabilityGovernor(CapabilityBudgetPolicy(max_budget_cost=1, max_total_calls=10))
        spec = _spec(budget_cost=2)
        decision = governor.check(spec)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error_type, "budget_exhausted")

    def test_approval_required_is_blocked(self) -> None:
        governor = CapabilityGovernor(CapabilityBudgetPolicy(max_budget_cost=10, max_total_calls=10))
        spec = _spec(approval_required=True)
        decision = governor.check(spec)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error_type, "approval_required")
        self.assertTrue(governor.check(spec, approval_granted=True).allowed)


if __name__ == "__main__":
    unittest.main()
