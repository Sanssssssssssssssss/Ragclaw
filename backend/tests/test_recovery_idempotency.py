from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.capabilities import build_tools_and_registry
from src.backend.capabilities.types import CapabilityResult
from src.backend.decision.execution_strategy import ExecutionStrategy
from src.backend.decision.lightweight_router import RoutingDecision
from src.backend.decision.skill_gate import SkillDecision
from src.backend.observability.trace_store import RunTraceStore
from src.backend.orchestration.checkpointing import checkpoint_store
from src.backend.runtime.execution_support import HarnessExecutionSupport
from src.backend.runtime.executors import HarnessExecutors
from src.backend.runtime.policy import SessionSerialQueue
from src.backend.runtime.runtime import HarnessRuntime, RuntimeDependencies


class _ToolMessage:
    def __init__(self, *, message_type: str, content: str = "", tool_calls=None, tool_call_id: str = "", name: str = "") -> None:
        self.type = message_type
        self.content = content
        self.tool_calls = list(tool_calls or [])
        self.tool_call_id = tool_call_id
        self.name = name


class _SingleToolAgent:
    def __init__(self, *, tool, tool_name: str, tool_args: dict[str, str]) -> None:
        self._tool = tool
        self._tool_name = tool_name
        self._tool_args = dict(tool_args)

    async def astream(self, _inputs, stream_mode=None):
        call_id = f"{self._tool_name}-call"
        yield (
            "updates",
            {
                "tool_node": {
                    "messages": [
                        _ToolMessage(
                            message_type="ai",
                            tool_calls=[{"id": call_id, "name": self._tool_name, "args": dict(self._tool_args)}],
                        )
                    ]
                }
            },
        )
        output = await self._tool.ainvoke(dict(self._tool_args))
        yield (
            "updates",
            {
                "tool_node": {
                    "messages": [
                        _ToolMessage(
                            message_type="tool",
                            tool_call_id=call_id,
                            name=self._tool_name,
                            content=str(output or ""),
                        )
                    ]
                }
            },
        )


class _RecoveryExecutionSupport(HarnessExecutionSupport):
    def __init__(self, agent_manager, *, tool_name: str = "", tool_args: dict[str, str] | None = None, model_answer: str = "") -> None:
        super().__init__(agent_manager)
        self._tool_name = tool_name
        self._tool_args = dict(tool_args or {})
        self._model_answer = model_answer

    def build_tool_agent(self, *, extra_instructions=None, tools_override=None):
        tools = list(tools_override or self._agent.tools)
        tool = next(item for item in tools if getattr(item, "name", "") == self._tool_name)
        return _SingleToolAgent(tool=tool, tool_name=self._tool_name, tool_args=self._tool_args)

    async def astream_model_answer(self, messages, extra_instructions=None, system_prompt_override=None):
        final_text = self._model_answer or "Recovered capability result."
        yield {"type": "token", "content": final_text}
        yield {"type": "done", "content": final_text, "usage": {"input_tokens": 5, "output_tokens": 4}}


class _ExplicitRecoveryAgentManager:
    def __init__(self, root: Path) -> None:
        self.base_dir = root
        self.tools, self._capability_registry = build_tools_and_registry(root)

    def create_execution_support(self):
        return HarnessExecutionSupport(self)

    def get_capability_registry(self):
        return self._capability_registry

    async def resolve_routing(self, message, history):
        return (
            ExecutionStrategy(allow_tools=True, allow_knowledge=False, allow_retrieval=False),
            RoutingDecision(
                intent="web_lookup",
                needs_tools=True,
                needs_retrieval=False,
                allowed_tools=("mcp_web_fetch_url",),
                confidence=1.0,
                reason_short="explicit web mcp recovery test",
                source="test",
                subtype="",
            ),
        )

    def decide_skill(self, message, history, strategy, routing_decision):
        return SkillDecision(False, "", 0.0, "no skill")

    def _runtime_rag_mode(self) -> bool:
        return False

    def _resolve_tools_for_strategy(self, strategy):
        return [tool for tool in self.tools if getattr(tool, "name", "") == "mcp_web_fetch_url"]

    def _build_messages(self, history):
        return []


class _HitlRecoveryAgentManager:
    def __init__(self, root: Path, *, model_answer: str = "The result is 4.") -> None:
        self.base_dir = root
        self.tools, self._capability_registry = build_tools_and_registry(root)
        self._support = _RecoveryExecutionSupport(
            self,
            tool_name="python_repl",
            tool_args={"code": "print(2 + 2)"},
            model_answer=model_answer,
        )

    def create_execution_support(self):
        return self._support

    def get_capability_registry(self):
        return self._capability_registry

    async def resolve_routing(self, message, history):
        return (
            ExecutionStrategy(allow_tools=True, allow_knowledge=False, allow_retrieval=False),
            RoutingDecision(
                intent="workspace_file_ops",
                needs_tools=True,
                needs_retrieval=False,
                allowed_tools=("python_repl",),
                confidence=1.0,
                reason_short="hitl recovery test",
                source="test",
                subtype="",
            ),
        )

    def decide_skill(self, message, history, strategy, routing_decision):
        return SkillDecision(False, "", 0.0, "no skill")

    def _runtime_rag_mode(self) -> bool:
        return False

    def _resolve_tools_for_strategy(self, strategy):
        return [tool for tool in self.tools if getattr(tool, "name", "") == "python_repl"]

    def _build_messages(self, history):
        return []


def _event_names(events) -> list[str]:
    return [event.name for event in events]


class RecoveryIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup)
        self.root = Path(self.temp_dir.name)
        checkpoint_store.configure_for_base_dir(self.root)
        self.runtime = HarnessRuntime(
            RuntimeDependencies(
                trace_store=RunTraceStore(self.root / "runs"),
                queue=SessionSerialQueue(lambda: "2026-04-08T12:00:00Z"),
                now_factory=lambda: "2026-04-08T12:00:00Z",
            )
        )

    async def _cleanup(self) -> None:
        checkpoint_store.configure_for_base_dir(BACKEND_DIR)
        self.temp_dir.cleanup()

    async def test_timeout_retries_once_then_falls_back_without_infinite_loop(self) -> None:
        manager = _ExplicitRecoveryAgentManager(self.root)
        web_tool = next(tool for tool in manager.tools if getattr(tool, "name", "") == "mcp_web_fetch_url")
        attempts = {"count": 0}

        async def _fail_timeout(_payload):
            attempts["count"] += 1
            return CapabilityResult(
                status="failed",
                payload={},
                partial=False,
                error_type="timeout",
                error_message="simulated timeout",
                retryable=True,
            )

        object.__setattr__(web_tool._inner_tool, "aexecute_capability", _fail_timeout)
        executor = HarnessExecutors(manager)
        events = [
            event
            async for event in self.runtime.run_with_executor(
                user_message="Use Web MCP only, fetch https://example.com/, and tell me the result.",
                session_id="session-timeout-recovery",
                source="chat_api",
                executor=executor,
                history=[],
                thread_id="session-timeout-recovery",
            )
        ]
        trace = self.runtime._deps.trace_store.read_trace(events[0].run_id)  # noqa: SLF001
        names = _event_names(events)

        self.assertEqual(attempts["count"], 2)
        self.assertEqual(names.count("capability.started"), 2)
        self.assertEqual(names.count("capability.failed"), 2)
        self.assertEqual(names.count("recovery.retrying"), 1)
        self.assertIn("recovery.fallback", names)
        self.assertNotIn("recovery.escalated", names)
        self.assertIn("simulated timeout", trace["outcome"]["final_answer"])
        self.assertIn("retried once", trace["outcome"]["final_answer"])

    async def test_capability_unavailable_falls_back_for_low_risk_capability(self) -> None:
        manager = _ExplicitRecoveryAgentManager(self.root)
        web_tool = next(tool for tool in manager.tools if getattr(tool, "name", "") == "mcp_web_fetch_url")

        async def _fail_unavailable(_payload):
            return CapabilityResult(
                status="failed",
                payload={},
                partial=False,
                error_type="capability_unavailable",
                error_message="simulated unavailable",
                retryable=False,
            )

        object.__setattr__(web_tool._inner_tool, "aexecute_capability", _fail_unavailable)
        executor = HarnessExecutors(manager)
        events = [
            event
            async for event in self.runtime.run_with_executor(
                user_message="Use Web MCP only, fetch https://example.com/, and tell me the result.",
                session_id="session-unavailable-recovery",
                source="chat_api",
                executor=executor,
                history=[],
                thread_id="session-unavailable-recovery",
            )
        ]
        trace = self.runtime._deps.trace_store.read_trace(events[0].run_id)  # noqa: SLF001
        names = _event_names(events)

        self.assertIn("recovery.fallback", names)
        self.assertNotIn("hitl.requested", names)
        self.assertIn("simulated unavailable", trace["outcome"]["final_answer"])

    async def test_recovery_escalation_reuses_hitl_and_resume_executes_once(self) -> None:
        manager = _HitlRecoveryAgentManager(self.root)
        python_tool = next(tool for tool in manager.tools if getattr(tool, "name", "") == "python_repl")
        object.__setattr__(python_tool._capability_spec, "approval_required", False)
        object.__setattr__(manager.get_capability_registry().get("python_repl"), "approval_required", False)
        attempts = {"count": 0}

        async def _sequenced_python(_payload):
            attempts["count"] += 1
            if attempts["count"] == 1:
                return CapabilityResult(
                    status="failed",
                    payload={},
                    partial=False,
                    error_type="capability_unavailable",
                    error_message="python runtime unavailable",
                    retryable=False,
                )
            return CapabilityResult(status="success", payload={"text": "4"}, partial=False)

        object.__setattr__(python_tool._inner_tool, "aexecute_capability", _sequenced_python)

        initial_executor = HarnessExecutors(manager)
        initial_events = [
            event
            async for event in self.runtime.run_with_executor(
                user_message="Use python_repl only, calculate 2 + 2, and tell me the result.",
                session_id="session-hitl-recovery",
                source="chat_api",
                executor=initial_executor,
                history=[],
                thread_id="session-hitl-recovery",
            )
        ]
        initial_names = _event_names(initial_events)
        pending = checkpoint_store.pending_hitl(thread_id="session-hitl-recovery")

        self.assertIsNotNone(pending)
        self.assertIn("recovery.escalated", initial_names)
        self.assertIn("hitl.requested", initial_names)
        self.assertEqual(attempts["count"], 1)

        resume_executor = HarnessExecutors(
            manager,
            resume_checkpoint_id=pending.checkpoint_id,
            resume_thread_id="session-hitl-recovery",
            resume_source="hitl_api",
            resume_payload={"decision": "approve"},
        )
        resumed_events = [
            event
            async for event in self.runtime.run_with_executor(
                user_message="Use python_repl only, calculate 2 + 2, and tell me the result.",
                session_id="session-hitl-recovery",
                source="hitl_api",
                executor=resume_executor,
                history=[],
                thread_id="session-hitl-recovery",
                checkpoint_id=pending.checkpoint_id,
                resume_source="hitl_api",
                run_status="restoring",
            )
        ]
        resumed_names = _event_names(resumed_events)
        resumed_trace = self.runtime._deps.trace_store.read_trace(resumed_events[0].run_id)  # noqa: SLF001

        self.assertEqual(attempts["count"], 2)
        self.assertIn("checkpoint.resumed", resumed_names)
        self.assertIn("hitl.approved", resumed_names)
        self.assertIn("capability.completed", resumed_names)
        self.assertEqual(resumed_names.count("capability.completed"), 1)
        self.assertIn("The result is 4.", resumed_trace["outcome"]["final_answer"])
        self.assertIsNone(checkpoint_store.pending_hitl(thread_id="session-hitl-recovery"))


if __name__ == "__main__":
    unittest.main()
