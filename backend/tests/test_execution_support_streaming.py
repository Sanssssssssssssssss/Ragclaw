from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.runtime.execution_support import HarnessExecutionSupport


class _Chunk:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeModel:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def astream(self, _messages):
        for chunk in self._chunks:
            yield _Chunk(chunk)


class _FakeAgentManager:
    def __init__(self, *, chunks: list[str]) -> None:
        self.base_dir = BACKEND_DIR
        self._chunks = chunks

    def _runtime_rag_mode(self) -> bool:
        return False

    def _build_chat_model(self):
        return _FakeModel(self._chunks)


class ExecutionSupportStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_astream_model_answer_normalizes_cumulative_snapshots_to_suffixes(self) -> None:
        manager = _FakeAgentManager(chunks=["我将为您检索", "我将为您检索知识库", "我将为您检索知识库里的三一重工财报总结"])
        support = HarnessExecutionSupport(manager)  # type: ignore[arg-type]

        events: list[dict] = []
        async for event in support.astream_model_answer([{"role": "user", "content": "知识库里三一重工的财报总结"}]):
            events.append(event)

        token_events = [event for event in events if event.get("type") == "token"]
        done_event = next(event for event in events if event.get("type") == "done")

        self.assertEqual(
            [event.get("content") for event in token_events],
            ["我将为您检索", "知识库", "里的三一重工财报总结"],
        )
        self.assertEqual(done_event.get("content"), "我将为您检索知识库里的三一重工财报总结")

    async def test_astream_model_answer_keeps_incremental_chunks_unchanged(self) -> None:
        manager = _FakeAgentManager(chunks=["我将为您", "检索知识库", "里的三一重工财报总结"])
        support = HarnessExecutionSupport(manager)  # type: ignore[arg-type]

        events: list[dict] = []
        async for event in support.astream_model_answer([{"role": "user", "content": "知识库里三一重工的财报总结"}]):
            events.append(event)

        token_events = [event for event in events if event.get("type") == "token"]
        done_event = next(event for event in events if event.get("type") == "done")

        self.assertEqual(
            [event.get("content") for event in token_events],
            ["我将为您", "检索知识库", "里的三一重工财报总结"],
        )
        self.assertEqual(done_event.get("content"), "我将为您检索知识库里的三一重工财报总结")


if __name__ == "__main__":
    unittest.main()
