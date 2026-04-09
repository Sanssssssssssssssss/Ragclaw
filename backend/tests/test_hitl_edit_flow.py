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


class _EditAgentManager:
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
                intent="workspace_file_ops",
                needs_tools=True,
                needs_retrieval=False,
                allowed_tools=("python_repl",),
                confidence=1.0,
                reason_short="hitl edit test",
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


class HitlEditFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup)
        self.root = Path(self.temp_dir.name)
        checkpoint_store.configure_for_base_dir(self.root)
        self.manager = _EditAgentManager(self.root)
        self.runtime = HarnessRuntime(
            RuntimeDependencies(
                trace_store=RunTraceStore(self.root / "runs"),
                queue=SessionSerialQueue(lambda: "2026-04-08T12:00:00Z"),
                now_factory=lambda: "2026-04-08T12:00:00Z",
            )
        )
        self.session_id = "session-hitl-edit"
        self.message = "Use python_repl only. Run print(2 + 2). Then let me edit the payload and continue."

    async def _cleanup(self) -> None:
        checkpoint_store.clear_pending_hitl(thread_id=self.session_id)
        checkpoint_store.configure_for_base_dir(BACKEND_DIR)
        self.temp_dir.cleanup()

    async def test_edit_resumes_with_edited_input_snapshot(self) -> None:
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
        self.assertEqual(pending.proposed_input, {"code": "print(2 + 2)"})

        resume_executor = HarnessExecutors(
            self.manager,
            resume_checkpoint_id=pending.checkpoint_id,
            resume_thread_id=self.session_id,
            resume_source="hitl_api",
            resume_payload={"decision": "edit", "edited_input": {"code": "print(3 + 4)"}},
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
        self.assertIn("hitl.edited", names)
        self.assertIn("tool.started", names)
        self.assertIn("tool.completed", names)
        self.assertIn("capability.completed", names)
        trace = self.runtime._deps.trace_store.read_trace(resume_events[0].run_id)  # noqa: SLF001
        self.assertEqual(trace["outcome"]["final_answer"], "7")
        decision = checkpoint_store.get_hitl_decision(thread_id=self.session_id, checkpoint_id=pending.checkpoint_id)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.decision, "edit")
        self.assertEqual(decision.edited_input_snapshot, {"code": "print(3 + 4)"})


if __name__ == "__main__":
    unittest.main()
