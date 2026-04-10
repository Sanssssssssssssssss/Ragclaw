from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.capabilities.governance import CapabilityBudgetPolicy, CapabilityGovernor
from src.backend.capabilities.invocation import CapabilityRuntimeContext, capability_runtime_scope, invoke_capability
from src.backend.capabilities.registry import CapabilityRegistry
from src.backend.capabilities.types import CapabilityResult, CapabilityRetryPolicy, CapabilitySpec
from src.backend.observability.otel import configure_otel, shutdown_otel
from src.backend.observability.trace_store import RunTraceStore
from src.backend.runtime.policy import SessionSerialQueue
from src.backend.runtime.runtime import HarnessRuntime, RuntimeDependencies


class _FakeExecutor:
    async def execute(self, runtime, handle, *, message: str, history: list[dict[str, object]]) -> None:
        await runtime.emit(
            handle,
            "answer.completed",
            {"segment_index": 0, "content": f"echo:{message}", "final": True},
        )


class _RuntimeHandle:
    def __init__(self) -> None:
        self.run_id = "run-tool"
        self.metadata = type("_Meta", (), {"session_id": "session-tool", "thread_id": "thread-tool"})()


class _CapabilityRuntime:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def now(self) -> str:
        return "2026-04-10T12:00:00Z"

    async def emit(self, handle, name: str, payload: dict) -> None:
        self.events.append((name, dict(payload)))

    def record_internal_event(self, run_id: str, name: str, payload: dict) -> None:
        self.events.append((name, dict(payload)))


class OTelTracingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.exporter = InMemorySpanExporter()
        configure_otel(force=True, enable=True, span_exporter=self.exporter)

    def tearDown(self) -> None:
        shutdown_otel()

    async def test_harness_run_span_is_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = HarnessRuntime(
                RuntimeDependencies(
                    trace_store=RunTraceStore(Path(temp_dir) / "runs"),
                    queue=SessionSerialQueue(lambda: "2026-04-10T12:00:00Z"),
                )
            )
            events = []
            async for event in runtime.run_with_executor(
                user_message="hello",
                session_id="session-otel",
                executor=_FakeExecutor(),
                history=[],
            ):
                events.append(event.name)

        spans = self.exporter.get_finished_spans()
        harness_span = next(span for span in spans if span.name == "harness.run")
        self.assertIn("run.started", events)
        self.assertIn("run.completed", events)
        self.assertEqual(harness_span.attributes["session_id"], "session-otel")
        self.assertIn("run_id", harness_span.attributes)

    async def test_execute_tool_span_is_emitted(self) -> None:
        runtime = _CapabilityRuntime()
        spec = CapabilitySpec(
            capability_id="tool.filesystem.read",
            capability_type="tool",
            display_name="Filesystem read",
            description="Read a file",
            when_to_use="Use when a file needs to be read.",
            when_not_to_use="Do not use for writes.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            error_schema={"type": "object"},
            risk_level="low",
            timeout_seconds=5,
            approval_required=False,
            retry_policy=CapabilityRetryPolicy(max_retries=0, backoff_seconds=0.0),
            budget_cost=1,
        )
        registry = CapabilityRegistry({spec.capability_id: spec})
        context = CapabilityRuntimeContext(
            runtime=runtime,
            handle=_RuntimeHandle(),
            registry=registry,
            governor=CapabilityGovernor(CapabilityBudgetPolicy(max_budget_cost=10, max_total_calls=10)),
        )

        async def _execute(payload: dict[str, object]) -> CapabilityResult:
            return CapabilityResult(status="success", payload={"text": f"ok:{payload['path']}"})

        async with capability_runtime_scope(context):
            result = await invoke_capability(
                spec=spec,
                payload={"path": "docs/readme.md"},
                execute_async=_execute,
            )

        spans = self.exporter.get_finished_spans()
        tool_span = next(span for span in spans if span.name == "execute_tool")
        self.assertEqual(result.status, "success")
        self.assertEqual(tool_span.attributes["capability_id"], "tool.filesystem.read")
        self.assertEqual(tool_span.attributes["tool_name"], "tool.filesystem.read")


if __name__ == "__main__":
    unittest.main()
