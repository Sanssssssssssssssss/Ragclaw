from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from graph.agent import AgentManager


async def collect_events(manager: AgentManager, message: str) -> list[dict]:
    events: list[dict] = []
    async for event in manager.astream(message, []):
        events.append(event)
    return events


class FakeAgent:
    def __init__(self, events):
        self._events = events

    async def astream(self, *_args, **_kwargs):
        for item in self._events:
            yield item


class AgentConstraintTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.manager = AgentManager()
        self.manager.initialize(BACKEND_DIR)

    def test_chinese_knowledge_queries_route_to_knowledge(self) -> None:
        self.assertTrue(
            self.manager._is_knowledge_query(
                "根据知识库，哪份文本通过删除博客文章的例子解释了 CSRF？请给出来源路径。"
            )
        )
        self.assertTrue(
            self.manager._is_knowledge_query(
                "根据知识库，读取 Financial Report Data 里相关内容，并给出来源路径。"
            )
        )

    async def test_direct_answer_constraints_skip_tools_and_knowledge(self) -> None:
        knowledge_called = False

        async def fake_model_answer(_messages, extra_instructions=None):
            self.assertIsNotNone(extra_instructions)
            self.assertTrue(any("Do not call any tools" in item for item in extra_instructions))
            yield {"type": "token", "content": "RAG 是检索增强生成；"}
            yield {"type": "done", "content": "RAG 是检索增强生成；微调是更新模型参数。"}

        async def fake_knowledge_astream(_message):
            nonlocal knowledge_called
            knowledge_called = True
            if False:
                yield {}

        with (
            patch.object(self.manager, "_astream_model_answer", side_effect=fake_model_answer),
            patch("graph.agent.knowledge_orchestrator.astream", side_effect=fake_knowledge_astream),
        ):
            events = await collect_events(
                self.manager,
                "不要调用任何工具，也不要读取知识库。请直接用你自己的常识，简洁解释一下 RAG 和微调的区别，各用一句话说明。",
            )

        self.assertFalse(knowledge_called)
        self.assertFalse(any(event["type"] == "tool_start" for event in events))
        self.assertFalse(any(event["type"] == "retrieval" for event in events))
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("微调", events[-1]["content"])

    async def test_terminal_only_constraints_skip_knowledge_and_filter_tools(self) -> None:
        knowledge_called = False

        fake_agent = FakeAgent(
            [
                (
                    "updates",
                    {
                        "tool": {
                            "messages": [
                                SimpleNamespace(
                                    type="ai",
                                    tool_calls=[{"id": "1", "name": "terminal", "args": {"command": "Get-ChildItem"}}],
                                    content="",
                                )
                            ]
                        }
                    },
                ),
                (
                    "updates",
                    {
                        "tool_result": {
                            "messages": [
                                SimpleNamespace(
                                    type="tool",
                                    tool_call_id="1",
                                    name="terminal",
                                    content="a.txt\nb.txt",
                                )
                            ]
                        }
                    },
                ),
                (
                    "messages",
                    (
                        SimpleNamespace(content="共有 2 个文件：a.txt、b.txt。"),
                        {"langgraph_node": "model"},
                    ),
                ),
            ]
        )

        async def fake_knowledge_astream(_message):
            nonlocal knowledge_called
            knowledge_called = True
            if False:
                yield {}

        with (
            patch.object(self.manager, "_build_agent", return_value=fake_agent) as build_agent,
            patch("graph.agent.knowledge_orchestrator.astream", side_effect=fake_knowledge_astream),
        ):
            events = await collect_events(
                self.manager,
                "请只使用 terminal 工具，不要使用 python_repl、read_file 或知识库检索。列出 knowledge/Financial Report Data 目录下的所有文件名，并告诉我一共多少个文件。",
            )

        self.assertFalse(knowledge_called)
        self.assertEqual(build_agent.call_args.kwargs["tools_override"][0].name, "terminal")
        self.assertEqual(len(build_agent.call_args.kwargs["tools_override"]), 1)
        self.assertEqual([event["tool"] for event in events if event["type"] == "tool_start"], ["terminal"])
        self.assertFalse(any(event["type"] == "retrieval" for event in events))
        self.assertIn("共有 2 个文件", events[-1]["content"])

    async def test_tool_success_without_final_answer_uses_fallback_summary(self) -> None:
        fake_agent = FakeAgent(
            [
                (
                    "messages",
                    (
                        SimpleNamespace(content="我来使用 python_repl 读取并处理这个 JSON 文件。"),
                        {"langgraph_node": "model"},
                    ),
                ),
                (
                    "updates",
                    {
                        "tool": {
                            "messages": [
                                SimpleNamespace(
                                    type="ai",
                                    tool_calls=[{"id": "1", "name": "python_repl", "args": {"code": "print('ok')"}}],
                                    content="",
                                )
                            ]
                        }
                    },
                ),
                (
                    "updates",
                    {
                        "tool_result": {
                            "messages": [
                                SimpleNamespace(
                                    type="tool",
                                    tool_call_id="1",
                                    name="python_repl",
                                    content="总记录数: 120\n1. 如何订购\n2. 我如何查看我的状态?\n3. 为什么我在我的帐户中找不到我的订单?",
                                )
                            ]
                        }
                    },
                ),
            ]
        )

        async def fake_model_answer(_messages, extra_instructions=None):
            self.assertIsNotNone(extra_instructions)
            self.assertTrue(any("Do not call more tools" in item for item in extra_instructions))
            yield {"type": "token", "content": "FAQ JSON 中共有 120 条记录。"}
            yield {
                "type": "done",
                "content": "FAQ JSON 中共有 120 条记录，前 3 条 question 分别是：如何订购、我如何查看我的状态、为什么我在我的帐户中找不到我的订单？",
            }

        with (
            patch.object(self.manager, "_build_agent", return_value=fake_agent),
            patch.object(self.manager, "_astream_model_answer", side_effect=fake_model_answer),
        ):
            events = await collect_events(
                self.manager,
                "读取 knowledge/E-commerce Data/faq.json，统计记录数，并给出前 3 条 question。",
            )

        self.assertEqual([event["tool"] for event in events if event["type"] == "tool_start"], ["python_repl"])
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("120", events[-1]["content"])
        self.assertIn("如何订购", events[-1]["content"])


if __name__ == "__main__":
    unittest.main()
