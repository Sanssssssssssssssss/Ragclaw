from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from harness.capability_governance import CapabilityBudgetPolicy, CapabilityGovernor
from harness.capability_invocation import CapabilityRuntimeContext, capability_runtime_scope, invoke_capability
from harness.capability_registry import CapabilityRegistry
from harness.capability_types import CapabilityResult, CapabilityRetryPolicy, CapabilitySpec


def _spec(**overrides):
    base = {
        "capability_id": "fetch_url",
        "capability_type": "tool",
        "display_name": "Fetch URL",
        "description": "tool",
        "when_to_use": "web",
        "when_not_to_use": "workspace",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "error_schema": {"type": "object"},
        "risk_level": "medium",
        "timeout_seconds": 15,
        "retry_policy": CapabilityRetryPolicy(max_retries=0),
        "approval_required": False,
    }
    base.update(overrides)
    return CapabilitySpec(**base)


@dataclass(frozen=True)
class _Handle:
    run_id: str = "run-1"
    metadata: object = None

    def __post_init__(self):
        if self.metadata is None:
            object.__setattr__(self, "metadata", type("_Meta", (), {"session_id": "session-1"})())


class _Runtime:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def now(self) -> str:
        return "2026-04-04T12:00:00Z"

    async def emit(self, handle, name: str, payload: dict) -> None:
        self.events.append((name, dict(payload)))

    def record_internal_event(self, run_id: str, name: str, payload: dict) -> None:
        self.events.append((name, dict(payload)))


class CapabilityInvocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_and_completion_are_recorded(self) -> None:
        spec = _spec(retry_policy=CapabilityRetryPolicy(max_retries=1, backoff_seconds=0.0, retryable_error_types=("network_error",)))
        registry = CapabilityRegistry({spec.capability_id: spec})
        runtime = _Runtime()
        context = CapabilityRuntimeContext(
            runtime=runtime,
            handle=_Handle(),
            registry=registry,
            governor=CapabilityGovernor(CapabilityBudgetPolicy(max_budget_cost=10, max_total_calls=10)),
        )
        attempts = {"count": 0}

        async def _executor(_payload):
            if attempts["count"] == 0:
                attempts["count"] += 1
                return CapabilityResult(
                    status="failed",
                    payload={},
                    partial=False,
                    error_type="network_error",
                    error_message="temporary",
                    retryable=True,
                )
            return CapabilityResult(status="success", payload={"text": "ok"})

        async with capability_runtime_scope(context):
            result = await invoke_capability(spec=spec, payload={"url": "https://example.com"}, execute_async=_executor)

        event_names = [name for name, _payload in runtime.events]
        self.assertEqual(result.status, "success")
        self.assertIn("capability.started", event_names)
        self.assertIn("capability.retry", event_names)
        self.assertIn("capability.completed", event_names)

    async def test_budget_block_emits_blocked_event(self) -> None:
        spec = _spec(budget_cost=2)
        registry = CapabilityRegistry({spec.capability_id: spec})
        runtime = _Runtime()
        context = CapabilityRuntimeContext(
            runtime=runtime,
            handle=_Handle(),
            registry=registry,
            governor=CapabilityGovernor(CapabilityBudgetPolicy(max_budget_cost=1, max_total_calls=10)),
        )

        async def _executor(_payload):
            return CapabilityResult(status="success", payload={"text": "ok"})

        async with capability_runtime_scope(context):
            result = await invoke_capability(spec=spec, payload={"url": "https://example.com"}, execute_async=_executor)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error_type, "budget_exhausted")
        self.assertEqual(runtime.events[0][0], "capability.blocked")


if __name__ == "__main__":
    unittest.main()
