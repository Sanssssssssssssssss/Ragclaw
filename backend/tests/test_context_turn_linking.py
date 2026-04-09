from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.orchestration.executor import HarnessLangGraphOrchestrator, _ExecutionBindings
from src.backend.context.store import context_store


@dataclass(frozen=True)
class _FakeMetadata:
    run_id: str
    session_id: str
    run_status: str = "fresh"
    checkpoint_id: str = ""
    resume_source: str = ""
    orchestration_engine: str = "langgraph"


@dataclass(frozen=True)
class _FakeHandle:
    metadata: _FakeMetadata

    @property
    def run_id(self) -> str:
        return self.metadata.run_id


class _FakeRuntime:
    def __init__(self, *, segment_index: int, now_value: str) -> None:
        self._segment_index = segment_index
        self._now_value = now_value

    def current_segment_index(self, _handle) -> int:
        return self._segment_index

    async def emit(self, _handle, _name: str, _payload: dict[str, object]):
        return None

    def now(self) -> str:
        return self._now_value


class _FakeExecution:
    async def astream_model_answer(self, _messages, **_kwargs):
        yield {
            "type": "done",
            "content": "Final answer",
            "usage": {"input_tokens": 21, "output_tokens": 7},
        }


class _FakeAgentManager:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def get_capability_registry(self):
        raise AssertionError("capability registry should not be needed for direct answer turn linking")


class ContextTurnLinkingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)

    async def asyncTearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    async def test_direct_answer_snapshot_uses_run_id_plus_segment_index(self) -> None:
        orchestrator = HarnessLangGraphOrchestrator(
            _FakeAgentManager(self.base_dir),
            execution_support=_FakeExecution(),
        )
        orchestrator._bindings = _ExecutionBindings(  # type: ignore[attr-defined]
            runtime=_FakeRuntime(segment_index=3, now_value="2026-04-09T12:00:00Z"),
            handle=_FakeHandle(_FakeMetadata(run_id="run-link", session_id="session-link")),
            context=SimpleNamespace(),
        )

        state = {
            "run_id": "run-link",
            "session_id": "session-link",
            "thread_id": "thread-link",
            "user_message": "Please answer directly.",
            "history": [{"role": "user", "content": "Earlier context"}],
            "working_memory": {"current_goal": "answer directly", "latest_user_intent": "direct answer"},
            "episodic_summary": {"key_facts": ["Keep it concise."]},
            "checkpoint_meta": {"updated_at": "2026-04-09T12:00:00Z", "run_status": "fresh"},
        }

        await orchestrator.direct_answer_node(state)

        stored = context_store.get_context_turn_snapshot(turn_id="run-link:3", session_id="session-link")

        self.assertIsNotNone(stored)
        self.assertEqual(stored.turn_id, "run-link:3")  # type: ignore[union-attr]
        self.assertEqual(stored.segment_index, 3)  # type: ignore[union-attr]
        self.assertEqual(stored.path_type, "direct_answer")  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
