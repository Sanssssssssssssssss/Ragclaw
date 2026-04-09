from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.backend.capabilities import build_tools_and_registry
from src.backend.decision.execution_strategy import ExecutionStrategy
from src.backend.decision.lightweight_router import RoutingDecision
from src.backend.decision.skill_gate import SkillDecision
from src.backend.observability.trace_store import RunTraceStore
from src.backend.runtime.executors import HarnessExecutors
from src.backend.runtime.policy import SessionSerialQueue
from src.backend.runtime.runtime import HarnessRuntime, RuntimeDependencies


class _NoToolAgentSupport:
    def astream_model_answer(self, *args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("explicit Filesystem MCP path should not stream a model answer first")

    def build_tool_agent(self, *args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("explicit Filesystem MCP path should bypass the tool agent")


class _FakeAgentManager:
    def __init__(self, root: Path) -> None:
        self.base_dir = root
        self.tools, self._capability_registry = build_tools_and_registry(root)

    def create_execution_support(self):
        return _NoToolAgentSupport()

    def get_capability_registry(self):
        return self._capability_registry

    async def resolve_routing(self, message, history):
        return (
            ExecutionStrategy(allow_tools=True, allow_knowledge=False, allow_retrieval=False),
            RoutingDecision(
                intent="workspace_file_ops",
                needs_tools=True,
                needs_retrieval=False,
                allowed_tools=("mcp_filesystem_read_file",),
                confidence=1.0,
                reason_short="test",
                source="test",
                subtype="read_existing_file",
            ),
        )

    def decide_skill(self, message, history, strategy, routing_decision):
        return SkillDecision(False, "", 0.0, "no skill")

    def _runtime_rag_mode(self) -> bool:
        return False

    def _resolve_tools_for_strategy(self, strategy):
        return [tool for tool in self.tools if getattr(tool, "name", "") == "mcp_filesystem_read_file"]

    def _build_messages(self, history):
        return []


class McpFastPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_filesystem_mcp_request_bypasses_tool_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mcp_manual").mkdir()
            (root / "mcp_manual" / "read_me.txt").write_text("hello from fast path", encoding="utf-8")

            manager = _FakeAgentManager(root)
            executor = HarnessExecutors(manager)
            runtime = HarnessRuntime(
                RuntimeDependencies(
                    trace_store=RunTraceStore(root / "runs"),
                    queue=SessionSerialQueue(lambda: "2026-04-06T12:00:00Z"),
                    now_factory=lambda: "2026-04-06T12:00:00Z",
                    run_id_factory=lambda: "run-fast-path",
                    event_id_factory=lambda: "evt-fast-path",
                )
            )

            events = [
                event
                async for event in runtime.run_with_executor(
                    user_message="Use Filesystem MCP only, read mcp_manual/read_me.txt, and tell me the exact content.",
                    session_id="session-fast-path",
                    source="chat_api",
                    executor=executor,
                    history=[],
                )
            ]
            names = [event.name for event in events]
            trace = runtime._deps.trace_store.read_trace("run-fast-path")  # noqa: SLF001

            self.assertIn("tool.started", names)
            self.assertIn("tool.completed", names)
            self.assertIn("capability.started", names)
            self.assertIn("capability.completed", names)
            self.assertEqual(trace["outcome"]["final_answer"], "hello from fast path")


if __name__ == "__main__":
    unittest.main()
