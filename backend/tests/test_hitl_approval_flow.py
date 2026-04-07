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


class _PythonToolAgent:
    def __init__(self, *, tool, output_override: str | None = None) -> None:
        self._tool = tool
        self._output_override = output_override

    async def astream(self, _inputs, stream_mode=None):
        call_id = "python-hitl-call"
        args = {"code": "print(2 + 2)"}
        yield (
            "updates",
            {
                "tool_node": {
                    "messages": [
                        _ToolMessage(
                            message_type="ai",
                            tool_calls=[{"id": call_id, "name": "python_repl", "args": args}],
                        )
                    ]
                }
            },
        )
        output = self._output_override if self._output_override is not None else await self._tool.ainvoke(args)
        yield (
            "updates",
            {
                "tool_node": {
                    "messages": [
                        _ToolMessage(
                            message_type="tool",
                            tool_call_id=call_id,
                            name="python_repl",
                            content=str(output or ""),
                        )
                    ]
                }
            },
        )


class _HitlExecutionSupport(HarnessExecutionSupport):
    def __init__(self, agent_manager) -> None:
        super().__init__(agent_manager)
        self._tool = next(tool for tool in agent_manager.tools if getattr(tool, "name", "") == "python_repl")

    def build_tool_agent(self, *, extra_instructions=None, tools_override=None):
        return _PythonToolAgent(tool=self._tool)

    async def astream_model_answer(self, messages, extra_instructions=None, system_prompt_override=None):
        final_text = "The result is 4."
        yield {"type": "token", "content": final_text}
        yield {"type": "done", "content": final_text, "usage": {"input_tokens": 5, "output_tokens": 4}}


class _HitlAgentManager:
    def __init__(self, root: Path) -> None:
        self.base_dir = root
        self.tools, self._capability_registry = build_tools_and_registry(root)

    def create_execution_support(self):
        return _HitlExecutionSupport(self)

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
                reason_short="hitl test",
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


class HitlApprovalFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup)
        self.root = Path(self.temp_dir.name)
        self.manager = _HitlAgentManager(self.root)
        self.runtime = HarnessRuntime(
            RuntimeDependencies(
                trace_store=RunTraceStore(self.root / "runs"),
                queue=SessionSerialQueue(lambda: "2026-04-07T12:00:00Z"),
                now_factory=lambda: "2026-04-07T12:00:00Z",
            )
        )
        self.session_id = "session-hitl"
        self.message = "Use python_repl only, calculate 2 + 2, and tell me the result."

    async def _cleanup(self) -> None:
        checkpoint_store.clear_pending_hitl(thread_id=self.session_id)
        self.temp_dir.cleanup()

    async def test_approval_required_capability_triggers_interrupt(self) -> None:
        executor = HarnessExecutors(self.manager)
        events = [
            event
            async for event in self.runtime.run_with_executor(
                user_message=self.message,
                session_id=self.session_id,
                source="chat_api",
                executor=executor,
                history=[],
                thread_id=self.session_id,
            )
        ]
        names = [event.name for event in events]
        self.assertIn("hitl.requested", names)
        self.assertIn("checkpoint.interrupted", names)
        self.assertNotIn("tool.started", names)
        pending = checkpoint_store.pending_hitl(thread_id=self.session_id)
        self.assertIsNotNone(pending)
        self.assertEqual(pending.capability_id, "python_repl")
        self.assertEqual(pending.thread_id, self.session_id)

    async def test_approve_resumes_and_executes_capability(self) -> None:
        first_executor = HarnessExecutors(self.manager)
        first_events = [
            event
            async for event in self.runtime.run_with_executor(
                user_message=self.message,
                session_id=self.session_id,
                source="chat_api",
                executor=first_executor,
                history=[],
                thread_id=self.session_id,
            )
        ]
        pending = checkpoint_store.pending_hitl(thread_id=self.session_id)
        self.assertIsNotNone(pending)
        resume_executor = HarnessExecutors(
            self.manager,
            resume_checkpoint_id=pending.checkpoint_id,
            resume_thread_id=self.session_id,
            resume_source="hitl_api",
            resume_payload={"decision": "approve"},
        )
        resume_events = [
            event
            async for event in self.runtime.run_with_executor(
                user_message=self.message,
                session_id=self.session_id,
                source="hitl_api",
                executor=resume_executor,
                history=[],
                thread_id=self.session_id,
                checkpoint_id=pending.checkpoint_id,
                resume_source="hitl_api",
                run_status="restoring",
            )
        ]
        names = [event.name for event in resume_events]
        self.assertIn("checkpoint.resumed", names)
        self.assertIn("hitl.approved", names)
        self.assertIn("tool.started", names)
        self.assertIn("tool.completed", names)
        self.assertIn("capability.completed", names)
        trace = self.runtime._deps.trace_store.read_trace(resume_events[0].run_id)  # noqa: SLF001
        self.assertEqual(trace["outcome"]["final_answer"], "The result is 4.")
        self.assertEqual(trace["outcome"]["thread_id"], self.session_id)
        self.assertEqual(trace["outcome"]["run_status"], "resumed")
        self.assertIsNone(checkpoint_store.pending_hitl(thread_id=self.session_id))
        self.assertIn("hitl.requested", [event.name for event in first_events])

    async def test_reject_resumes_and_skips_capability(self) -> None:
        first_executor = HarnessExecutors(self.manager)
        _ = [
            event
            async for event in self.runtime.run_with_executor(
                user_message=self.message,
                session_id=self.session_id,
                source="chat_api",
                executor=first_executor,
                history=[],
                thread_id=self.session_id,
            )
        ]
        pending = checkpoint_store.pending_hitl(thread_id=self.session_id)
        self.assertIsNotNone(pending)
        resume_executor = HarnessExecutors(
            self.manager,
            resume_checkpoint_id=pending.checkpoint_id,
            resume_thread_id=self.session_id,
            resume_source="hitl_api",
            resume_payload={"decision": "reject"},
        )
        resume_events = [
            event
            async for event in self.runtime.run_with_executor(
                user_message=self.message,
                session_id=self.session_id,
                source="hitl_api",
                executor=resume_executor,
                history=[],
                thread_id=self.session_id,
                checkpoint_id=pending.checkpoint_id,
                resume_source="hitl_api",
                run_status="restoring",
            )
        ]
        names = [event.name for event in resume_events]
        self.assertIn("hitl.rejected", names)
        self.assertIn("capability.blocked", names)
        self.assertNotIn("tool.started", names)
        trace = self.runtime._deps.trace_store.read_trace(resume_events[0].run_id)  # noqa: SLF001
        self.assertIn("rejected this approval request", trace["outcome"]["final_answer"])
        self.assertIsNone(checkpoint_store.pending_hitl(thread_id=self.session_id))


if __name__ == "__main__":
    unittest.main()
