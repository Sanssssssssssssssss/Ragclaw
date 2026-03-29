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
                "\u6839\u636e\u77e5\u8bc6\u5e93\uff0c\u54ea\u4efd\u6587\u672c\u901a\u8fc7\u5220\u9664\u535a\u5ba2\u6587\u7ae0\u7684\u4f8b\u5b50\u89e3\u91ca\u4e86 CSRF\uff1f\u8bf7\u7ed9\u51fa\u6765\u6e90\u8def\u5f84\u3002"
            )
        )
        self.assertTrue(
            self.manager._is_knowledge_query(
                "\u6839\u636e\u77e5\u8bc6\u5e93\uff0c\u8bfb\u53d6 Financial Report Data \u91cc\u76f8\u5173\u5185\u5bb9\uff0c\u5e76\u7ed9\u51fa\u6765\u6e90\u8def\u5f84\u3002"
            )
        )

    def test_negation_scaffold_does_not_embed_internal_reason_text(self) -> None:
        retrieval_result = SimpleNamespace(
            question_type="negation",
            evidences=[
                SimpleNamespace(
                    source_path="knowledge/Financial Report Data/\u822a\u5929\u52a8\u529b 2025 Q3.pdf",
                    locator="page 1",
                    snippet="\u5f52\u5c5e\u4e8e\u4e0a\u5e02\u516c\u53f8\u80a1\u4e1c\u7684\u51c0\u5229\u6da6 -36,128,235.45 \u5143",
                )
            ],
            reason="The knowledge index did not return enough direct negative evidence.",
            status="partial",
            fallback_used=False,
        )
        scaffold = self.manager._build_knowledge_scaffold(
            "\u6839\u636e\u77e5\u8bc6\u5e93\uff0c\u8bf4\u660e\u822a\u5929\u52a8\u529b 2025 Q3 \u5e76\u672a\u76c8\u5229\u7684\u8bc1\u636e\uff0c\u5e76\u7ed9\u51fa\u6765\u6e90\u3002",
            retrieval_result,
        )
        self.assertIn("direct_negative_evidence", scaffold)
        self.assertNotIn("The knowledge index", scaffold)

    async def test_direct_answer_constraints_skip_tools_and_knowledge(self) -> None:
        knowledge_called = False

        async def fake_model_answer(_messages, extra_instructions=None):
            self.assertIsNotNone(extra_instructions)
            self.assertTrue(any("Do not call any tools" in item for item in extra_instructions))
            yield {"type": "token", "content": "RAG \u662f\u68c0\u7d22\u589e\u5f3a\u751f\u6210\uff1b"}
            yield {"type": "done", "content": "RAG \u662f\u68c0\u7d22\u589e\u5f3a\u751f\u6210\uff1b\u5fae\u8c03\u662f\u66f4\u65b0\u6a21\u578b\u53c2\u6570\u3002"}

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
                "\u4e0d\u8981\u8c03\u7528\u4efb\u4f55\u5de5\u5177\uff0c\u4e5f\u4e0d\u8981\u8bfb\u53d6\u77e5\u8bc6\u5e93\u3002\u8bf7\u76f4\u63a5\u7528\u4f60\u81ea\u5df1\u7684\u5e38\u8bc6\uff0c\u7b80\u6d01\u89e3\u91ca\u4e00\u4e0b RAG \u548c\u5fae\u8c03\u7684\u533a\u522b\uff0c\u5404\u7528\u4e00\u53e5\u8bdd\u8bf4\u660e\u3002",
            )

        self.assertFalse(knowledge_called)
        self.assertFalse(any(event["type"] == "tool_start" for event in events))
        self.assertFalse(any(event["type"] == "retrieval" for event in events))
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("\u5fae\u8c03", events[-1]["content"])

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
                        SimpleNamespace(content="\u5171\u6709 2 \u4e2a\u6587\u4ef6\uff1aa.txt\u3001b.txt\u3002"),
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
                "\u8bf7\u53ea\u4f7f\u7528 terminal \u5de5\u5177\uff0c\u4e0d\u8981\u4f7f\u7528 python_repl\u3001read_file \u6216\u77e5\u8bc6\u5e93\u68c0\u7d22\u3002\u5217\u51fa knowledge/Financial Report Data \u76ee\u5f55\u4e0b\u7684\u6240\u6709\u6587\u4ef6\u540d\uff0c\u5e76\u544a\u8bc9\u6211\u4e00\u5171\u591a\u5c11\u4e2a\u6587\u4ef6\u3002",
            )

        self.assertFalse(knowledge_called)
        self.assertEqual(build_agent.call_args.kwargs["tools_override"][0].name, "terminal")
        self.assertEqual(len(build_agent.call_args.kwargs["tools_override"]), 1)
        self.assertEqual([event["tool"] for event in events if event["type"] == "tool_start"], ["terminal"])
        self.assertFalse(any(event["type"] == "retrieval" for event in events))
        self.assertIn("\u5171\u6709 2 \u4e2a\u6587\u4ef6", events[-1]["content"])

    async def test_tool_success_without_final_answer_uses_fallback_summary(self) -> None:
        fake_agent = FakeAgent(
            [
                (
                    "messages",
                    (
                        SimpleNamespace(content="\u6211\u6765\u4f7f\u7528 python_repl \u8bfb\u53d6\u5e76\u5904\u7406\u8fd9\u4e2a JSON \u6587\u4ef6\u3002"),
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
                                    content="\u603b\u8bb0\u5f55\u6570: 120\n1. \u5982\u4f55\u8ba2\u8d2d\n2. \u6211\u5982\u4f55\u67e5\u770b\u6211\u7684\u72b6\u6001?\n3. \u4e3a\u4ec0\u4e48\u6211\u5728\u6211\u7684\u5e10\u6237\u4e2d\u627e\u4e0d\u5230\u6211\u7684\u8ba2\u5355?",
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
            yield {"type": "token", "content": "FAQ JSON \u4e2d\u5171\u6709 120 \u6761\u8bb0\u5f55\u3002"}
            yield {
                "type": "done",
                "content": "FAQ JSON \u4e2d\u5171\u6709 120 \u6761\u8bb0\u5f55\uff0c\u524d 3 \u6761 question \u5206\u522b\u662f\uff1a\u5982\u4f55\u8ba2\u8d2d\u3001\u6211\u5982\u4f55\u67e5\u770b\u6211\u7684\u72b6\u6001\u3001\u4e3a\u4ec0\u4e48\u6211\u5728\u6211\u7684\u5e10\u6237\u4e2d\u627e\u4e0d\u5230\u6211\u7684\u8ba2\u5355\uff1f",
            }

        with (
            patch.object(self.manager, "_build_agent", return_value=fake_agent),
            patch.object(self.manager, "_astream_model_answer", side_effect=fake_model_answer),
        ):
            events = await collect_events(
                self.manager,
                "\u8bfb\u53d6 knowledge/E-commerce Data/faq.json\uff0c\u7edf\u8ba1\u8bb0\u5f55\u6570\uff0c\u5e76\u7ed9\u51fa\u524d 3 \u6761 question\u3002",
            )

        self.assertEqual([event["tool"] for event in events if event["type"] == "tool_start"], ["python_repl"])
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("120", events[-1]["content"])
        self.assertIn("\u5982\u4f55\u8ba2\u8d2d", events[-1]["content"])


if __name__ == "__main__":
    unittest.main()
