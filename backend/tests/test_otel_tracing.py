from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
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
from src.backend.context.assembler import ContextAssembler
from src.backend.context.store import context_store
from src.backend.observability.otel import configure_otel, shutdown_otel
from src.backend.observability.trace_store import RunTraceStore
from src.backend.orchestration.checkpointing import HitlDecisionRecord, PendingHitlRequest
from src.backend.orchestration.executor import HarnessLangGraphOrchestrator, _ExecutionBindings
from src.backend.orchestration.nodes.answer import build_direct_answer_node
from src.backend.runtime.policy import SessionSerialQueue
from src.backend.runtime.runtime import HarnessRuntime, RuntimeDependencies
from src.backend.decision.execution_strategy import ExecutionStrategy
from src.backend.decision.lightweight_router import RoutingDecision
from src.backend.decision.skill_gate import SkillDecision


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


@dataclass(frozen=True)
class _GraphMetadata:
    run_id: str
    session_id: str
    thread_id: str = "thread-otel"
    run_status: str = "fresh"
    checkpoint_id: str = ""
    resume_source: str = ""
    orchestration_engine: str = "langgraph"


@dataclass(frozen=True)
class _GraphHandle:
    metadata: _GraphMetadata

    @property
    def run_id(self) -> str:
        return self.metadata.run_id


class _GraphRuntime:
    def __init__(self, *, segment_index: int = 0, now_value: str = "2026-04-11T09:00:00Z") -> None:
        self._segment_index = segment_index
        self._now_value = now_value
        self.events: list[tuple[str, dict]] = []

    def current_segment_index(self, _handle) -> int:
        return self._segment_index

    def advance_answer_segment(self, _handle) -> int:
        self._segment_index += 1
        return self._segment_index

    async def emit(self, _handle, name: str, payload: dict[str, object]) -> None:
        self.events.append((name, dict(payload)))

    def now(self) -> str:
        return self._now_value

    def governor_for(self, _run_id: str):
        return SimpleNamespace(snapshot=lambda: {})


class _GraphExecution:
    async def astream_model_answer(self, _messages, **_kwargs):
        yield {
            "type": "done",
            "content": "otel answer",
            "usage": {"input_tokens": 13, "output_tokens": 3},
        }


class _GraphAgentManager:
    def __init__(self, base_dir: Path, registry: CapabilityRegistry | None = None) -> None:
        self.base_dir = base_dir
        self._registry = registry

    def _runtime_rag_mode(self) -> bool:
        return False

    async def resolve_routing(self, _message, _history):
        return (
            ExecutionStrategy(allow_tools=False, allow_knowledge=False, allow_retrieval=False),
            RoutingDecision(
                intent="direct_answer",
                needs_tools=False,
                needs_retrieval=False,
                allowed_tools=(),
                confidence=1.0,
                reason_short="direct",
                source="test",
                subtype="",
            ),
        )

    def decide_skill(self, _message, _history, _strategy, _decision):
        return SkillDecision(False, "", 0.0, "no skill")

    def get_capability_registry(self):
        if self._registry is None:
            raise AssertionError("capability registry was not expected for this test")
        return self._registry


class OTelTracingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.exporter = InMemorySpanExporter()
        configure_otel(force=True, enable=True, span_exporter=self.exporter)

    def tearDown(self) -> None:
        shutdown_otel()
        context_store.close()

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

    async def test_capability_invoke_and_tool_execute_spans_are_emitted(self) -> None:
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
        capability_span = next(span for span in spans if span.name == "capability.invoke")
        tool_span = next(span for span in spans if span.name == "tool.execute")
        self.assertEqual(result.status, "success")
        self.assertEqual(capability_span.attributes["capability_id"], "tool.filesystem.read")
        self.assertEqual(tool_span.attributes["capability_id"], "tool.filesystem.read")
        self.assertEqual(tool_span.attributes["tool_name"], "tool.filesystem.read")

    async def test_context_assemble_span_is_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "backend"
            base_dir.mkdir(parents=True, exist_ok=True)
            context_store.configure_for_base_dir(base_dir)
            assembler = ContextAssembler(base_dir=base_dir)

            assembly = assembler.assemble(
                path_kind="direct_answer",
                state={
                    "run_id": "run-context",
                    "session_id": "session-context",
                    "thread_id": "thread-context",
                    "user_message": "Summarize this state",
                    "history": [{"role": "user", "content": "Older turn"}],
                    "working_memory": {"current_goal": "summarize"},
                    "episodic_summary": {"key_facts": ["fact"]},
                    "checkpoint_meta": {"updated_at": "2026-04-11T09:00:00Z", "orchestration_engine": "langgraph"},
                },
                call_site="unit_test",
            )
            context_store.close()

        spans = self.exporter.get_finished_spans()
        context_span = next(span for span in spans if span.name == "context.assemble")
        self.assertEqual(assembly.path_kind, "direct_answer")
        self.assertEqual(context_span.attributes["context_path_type"], "direct_answer")
        self.assertEqual(context_span.attributes["call_site"], "unit_test")

    async def test_graph_node_span_is_emitted_for_direct_answer_node(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "backend"
            base_dir.mkdir(parents=True, exist_ok=True)
            context_store.configure_for_base_dir(base_dir)
            orchestrator = HarnessLangGraphOrchestrator(
                _GraphAgentManager(base_dir),
                execution_support=_GraphExecution(),
            )
            orchestrator._bindings = _ExecutionBindings(  # type: ignore[attr-defined]
                runtime=_GraphRuntime(segment_index=2),
                handle=_GraphHandle(_GraphMetadata(run_id="run-node", session_id="session-node")),
                context=SimpleNamespace(),
            )

            node = build_direct_answer_node(orchestrator)
            await node(
                {
                    "run_id": "run-node",
                    "session_id": "session-node",
                    "thread_id": "thread-node",
                    "user_message": "Answer directly.",
                    "history": [],
                    "working_memory": {"current_goal": "answer"},
                    "episodic_summary": {"key_facts": []},
                    "checkpoint_meta": {"updated_at": "2026-04-11T09:00:00Z", "run_status": "fresh"},
                }
            )
            context_store.close()

        spans = self.exporter.get_finished_spans()
        node_span = next(span for span in spans if span.name == "graph.node")
        self.assertEqual(node_span.attributes["node_name"], "direct_answer")
        self.assertEqual(node_span.attributes["run_id"], "run-node")

    async def test_checkpoint_and_hitl_spans_are_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "backend"
            base_dir.mkdir(parents=True, exist_ok=True)
            registry_spec = CapabilitySpec(
                capability_id="python_repl",
                capability_type="tool",
                display_name="Python REPL",
                description="Run Python",
                when_to_use="Use for calculations",
                when_not_to_use="Do not use for shell tasks",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                error_schema={"type": "object"},
                risk_level="high",
                timeout_seconds=5,
                approval_required=True,
                retry_policy=CapabilityRetryPolicy(max_retries=0, backoff_seconds=0.0),
                budget_cost=1,
            )
            registry = CapabilityRegistry({registry_spec.capability_id: registry_spec})
            orchestrator = HarnessLangGraphOrchestrator(
                _GraphAgentManager(base_dir, registry=registry),
                execution_support=_GraphExecution(),
            )
            runtime = _GraphRuntime(segment_index=1)
            handle = _GraphHandle(_GraphMetadata(run_id="run-hitl", session_id="session-hitl", checkpoint_id="cp-1", resume_source="hitl_api", run_status="resumed"))
            orchestrator._bindings = _ExecutionBindings(  # type: ignore[attr-defined]
                runtime=runtime,
                handle=handle,
                context=SimpleNamespace(approval_overrides=set(), governor=CapabilityGovernor(CapabilityBudgetPolicy(max_budget_cost=10, max_total_calls=10))),
            )

            checkpoint_summary = SimpleNamespace(checkpoint_id="cp-1", state_label="interrupted", created_at="2026-04-11T09:00:00Z", resume_eligible=True, thread_id="thread-hitl")
            pending_request = PendingHitlRequest(
                request_id="req-1",
                run_id="run-hitl",
                thread_id="thread-hitl",
                session_id="session-hitl",
                checkpoint_id="cp-1",
                capability_id="python_repl",
                capability_type="tool",
                display_name="Python REPL",
                risk_level="high",
                reason="Approval required",
                proposed_input={"code": "print(2 + 2)"},
                requested_at="2026-04-11T09:00:00Z",
                status="pending",
            )
            audited_decision = HitlDecisionRecord(
                decision_id="dec-1",
                request_id="req-1",
                decision="approve",
                actor_id="session:session-hitl",
                actor_type="session_user",
                decided_at="2026-04-11T09:01:00Z",
                resume_source="langgraph_resume",
                approved_input_snapshot={"code": "print(2 + 2)"},
                edited_input_snapshot={},
                rejected_input_snapshot={},
            )
            interrupt_payload = SimpleNamespace(
                value={
                    "run_id": "run-hitl",
                    "thread_id": "thread-hitl",
                    "session_id": "session-hitl",
                    "checkpoint_id": "cp-1",
                    "capability_id": "python_repl",
                    "capability_type": "tool",
                    "display_name": "Python REPL",
                    "risk_level": "high",
                    "reason": "Approval required",
                    "proposed_input": {"code": "print(2 + 2)"},
                }
            )

            with (
                patch("src.backend.orchestration.executor.checkpoint_store.latest_checkpoint", return_value=checkpoint_summary),
                patch("src.backend.orchestration.executor.checkpoint_store.get_checkpoint", return_value=checkpoint_summary),
                patch("src.backend.orchestration.executor.checkpoint_store.record_pending_hitl", return_value=(pending_request, True)),
                patch("src.backend.orchestration.executor.checkpoint_store.get_hitl_request", return_value=pending_request),
                patch("src.backend.orchestration.executor.checkpoint_store.get_hitl_decision", return_value=None),
                patch("src.backend.orchestration.executor.checkpoint_store.record_hitl_decision", return_value=(pending_request, audited_decision, True)),
                patch("src.backend.orchestration.executor.interrupt", return_value={"decision": "approve"}),
            ):
                await orchestrator._emit_checkpoint_created(runtime, handle, "thread-hitl")
                await orchestrator._emit_resume_events(runtime, handle, "thread-hitl")
                await orchestrator._emit_hitl_interrupt_if_needed(runtime, handle, "thread-hitl", {"__interrupt__": [interrupt_payload]})
                await orchestrator.capability_approval_node(
                    {
                        "run_id": "run-hitl",
                        "session_id": "session-hitl",
                        "thread_id": "thread-hitl",
                        "selected_capabilities": ["python_repl"],
                        "explicit_capability_payload": {"code": "print(2 + 2)"},
                        "checkpoint_meta": {
                            "checkpoint_id": "cp-1",
                            "resume_source": "hitl_api",
                            "run_status": "resumed",
                            "updated_at": "2026-04-11T09:00:00Z",
                        },
                    }
                )

        spans = self.exporter.get_finished_spans()
        span_names = {span.name for span in spans}
        self.assertIn("checkpoint.create", span_names)
        self.assertIn("checkpoint.resume", span_names)
        self.assertIn("hitl.request", span_names)
        self.assertIn("hitl.decision", span_names)

    async def test_http_request_span_is_emitted(self) -> None:
        from src.backend.api import app as backend_app

        with (
            patch.object(backend_app, "refresh_snapshot"),
            patch.object(backend_app.agent_manager, "initialize"),
            patch.object(backend_app.memory_indexer, "configure"),
            patch.object(backend_app.memory_indexer, "rebuild_index"),
            patch.object(backend_app.knowledge_indexer, "configure"),
            patch.object(backend_app, "_schedule_knowledge_warm_start"),
        ):
            with TestClient(backend_app.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        spans = self.exporter.get_finished_spans()
        request_span = next(span for span in spans if span.name == "http.request")
        self.assertEqual(request_span.attributes["http.method"], "GET")
        self.assertEqual(request_span.attributes["url.path"], "/health")
        self.assertEqual(request_span.attributes["http.status_code"], 200)


if __name__ == "__main__":
    unittest.main()
