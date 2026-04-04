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
from harness.execution_support import incremental_text


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


class FakeExecutionSupport:
    def __init__(self, *, model_answer=None, tool_agent=None) -> None:
        self._model_answer = model_answer
        self._tool_agent = tool_agent
        self.build_tool_agent_calls: list[dict[str, object]] = []

    async def astream_model_answer(self, messages, extra_instructions=None, system_prompt_override=None):
        if self._model_answer is None:
            raise AssertionError("unexpected model answer call")
        async for item in self._model_answer(messages, extra_instructions=extra_instructions, system_prompt_override=system_prompt_override):
            yield item

    def build_tool_agent(self, *, extra_instructions=None, tools_override=None):
        self.build_tool_agent_calls.append(
            {
                "extra_instructions": list(extra_instructions or []),
                "tools_override": list(tools_override or []),
            }
        )
        if self._tool_agent is None:
            raise AssertionError("unexpected tool agent call")
        return self._tool_agent

    def incremental_stream_text(self, previous: str, current: str) -> str:
        return incremental_text(previous, current)

    def tool_agent_instructions(self, strategy, skill_decision=None) -> list[str]:
        return list(strategy.to_instructions())

    def tool_results_context(self, recorded_tools: list[dict[str, str]]) -> str:
        blocks = ["[Tool execution results]"]
        for index, item in enumerate(recorded_tools, start=1):
            blocks.append(
                f"{index}. Tool: {item.get('tool', 'tool')}\n"
                f"Input: {item.get('input', '')}\n"
                f"Output:\n{item.get('output', '') or '[no output]'}"
            )
        return "\n\n".join(blocks)

    def needs_tool_result_fallback(self, final_content: str, recorded_tools: list[dict[str, str]]) -> bool:
        if not recorded_tools:
            return False
        normalized = str(final_content or "").strip().lower()
        if not normalized:
            return True
        return normalized.startswith("我来使用") or normalized.startswith("i'll use") or normalized.startswith("let me use")


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
        self.assertIn("negative_signal: direct", scaffold)
        self.assertNotIn("The knowledge index", scaffold)

    def test_compare_scaffold_extracts_company_slots_from_evidence(self) -> None:
        retrieval_result = SimpleNamespace(
            question_type="compare",
            entity_hints=["上汽集团", "三一重工"],
            evidences=[
                SimpleNamespace(
                    source_path="knowledge/Financial Report Data/上汽集团 2025 Q3.pdf",
                    locator="page 1 / table #57",
                    snippet="归属于上市公司股东的净利润 本报告期 2,083,357,266.67 元 同比 644.88% 年初至报告期末 8,100,867,339.79 元 同比 17.28%",
                ),
                SimpleNamespace(
                    source_path="knowledge/Financial Report Data/三一重工 2025 Q3.pdf",
                    locator="page 3 / table #98",
                    snippet="归属于上市公司股东的净利润 本报告期 1,919,279 千元 同比 48.18% 年初至报告期末 7,135,595 千元 同比 46.58%",
                ),
            ],
        )
        scaffold = self.manager._build_knowledge_scaffold(
            "根据知识库，对比上汽集团与三一重工 2025 Q3 的净利润变化情况，并给出来源。",
            retrieval_result,
        )
        self.assertIn("report_value_a: 2,083,357,266.67 元", scaffold)
        self.assertIn("report_value_b: 1,919,279 千元", scaffold)
        self.assertIn("ytd_value_b: 7,135,595 千元", scaffold)
        self.assertNotIn("report_value_b: 当前证据未显示", scaffold)

    def test_negation_scaffold_marks_weak_fragments_as_insufficient(self) -> None:
        retrieval_result = SimpleNamespace(
            question_type="negation",
            evidences=[
                SimpleNamespace(
                    source_path="knowledge/Financial Report Data/航天动力_2025_Q3.txt",
                    locator="段落 1",
                    snippet="利润总额 0 不适用 主要原因 本期确认中小投资者索赔损失增加所致",
                )
            ],
            reason="partial evidence",
            status="partial",
            fallback_used=False,
        )
        scaffold = self.manager._build_knowledge_scaffold(
            "根据知识库，说明航天动力 2025 Q3 并未盈利的证据，并给出来源。",
            retrieval_result,
        )
        self.assertIn("negative_signal: weak", scaffold)
        self.assertIn("利润总额 0 不适用", scaffold)
        self.assertNotIn("negative_signal: missing", scaffold)

    def test_multi_hop_scaffold_marks_missing_second_item(self) -> None:
        retrieval_result = SimpleNamespace(
            question_type="multi_hop",
            evidences=[
                SimpleNamespace(
                    source_path="knowledge/AI Knowledge/2026AI应用专题.pdf",
                    locator="page 10",
                    snippet='医疗相关产品：“依保儿”就医智能体，已与 8 省市卫健委合作。',
                )
            ],
        )
        scaffold = self.manager._build_knowledge_scaffold(
            "根据知识库，概括 AI 应用专题中两项医疗相关产品，并给出来源。",
            retrieval_result,
        )
        self.assertIn("mode: enumerated_items", scaffold)
        self.assertIn("found_item_count: 1", scaffold)
        self.assertIn("missing_constraints: item_2", scaffold)

    async def test_direct_answer_constraints_skip_tools_and_knowledge(self) -> None:
        knowledge_called = False

        async def fake_model_answer(_messages, extra_instructions=None, system_prompt_override=None):
            self.assertIsNotNone(extra_instructions)
            self.assertTrue(any("Do not call any tools" in item for item in extra_instructions))
            yield {"type": "token", "content": "RAG \u662f\u68c0\u7d22\u589e\u5f3a\u751f\u6210\uff1b"}
            yield {"type": "done", "content": "RAG \u662f\u68c0\u7d22\u589e\u5f3a\u751f\u6210\uff1b\u5fae\u8c03\u662f\u66f4\u65b0\u6a21\u578b\u53c2\u6570\u3002"}

        async def fake_knowledge_astream(_message):
            nonlocal knowledge_called
            knowledge_called = True
            if False:
                yield {}

        support = FakeExecutionSupport(model_answer=fake_model_answer)
        with (
            patch.object(self.manager, "create_execution_support", return_value=support),
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
        self.assertFalse(any(event["type"].startswith("_harness_") for event in events))

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

        support = FakeExecutionSupport(tool_agent=fake_agent)
        with (
            patch.object(self.manager, "create_execution_support", return_value=support),
            patch("graph.agent.knowledge_orchestrator.astream", side_effect=fake_knowledge_astream),
        ):
            events = await collect_events(
                self.manager,
                "\u8bf7\u53ea\u4f7f\u7528 terminal \u5de5\u5177\uff0c\u4e0d\u8981\u4f7f\u7528 python_repl\u3001read_file \u6216\u77e5\u8bc6\u5e93\u68c0\u7d22\u3002\u5217\u51fa knowledge/Financial Report Data \u76ee\u5f55\u4e0b\u7684\u6240\u6709\u6587\u4ef6\u540d\uff0c\u5e76\u544a\u8bc9\u6211\u4e00\u5171\u591a\u5c11\u4e2a\u6587\u4ef6\u3002",
            )

        self.assertFalse(knowledge_called)
        self.assertEqual(support.build_tool_agent_calls[0]["tools_override"][0].name, "terminal")
        self.assertEqual(len(support.build_tool_agent_calls[0]["tools_override"]), 1)
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

        async def fake_model_answer(_messages, extra_instructions=None, system_prompt_override=None):
            self.assertIsNotNone(extra_instructions)
            self.assertTrue(any("Do not call more tools" in item for item in extra_instructions))
            yield {"type": "token", "content": "FAQ JSON \u4e2d\u5171\u6709 120 \u6761\u8bb0\u5f55\u3002"}
            yield {
                "type": "done",
                "content": "FAQ JSON \u4e2d\u5171\u6709 120 \u6761\u8bb0\u5f55\uff0c\u524d 3 \u6761 question \u5206\u522b\u662f\uff1a\u5982\u4f55\u8ba2\u8d2d\u3001\u6211\u5982\u4f55\u67e5\u770b\u6211\u7684\u72b6\u6001\u3001\u4e3a\u4ec0\u4e48\u6211\u5728\u6211\u7684\u5e10\u6237\u4e2d\u627e\u4e0d\u5230\u6211\u7684\u8ba2\u5355\uff1f",
            }

        support = FakeExecutionSupport(tool_agent=fake_agent, model_answer=fake_model_answer)
        with patch.object(self.manager, "create_execution_support", return_value=support):
            events = await collect_events(
                self.manager,
                "\u8bfb\u53d6 knowledge/E-commerce Data/faq.json\uff0c\u7edf\u8ba1\u8bb0\u5f55\u6570\uff0c\u5e76\u7ed9\u51fa\u524d 3 \u6761 question\u3002",
            )

        self.assertEqual([event["tool"] for event in events if event["type"] == "tool_start"], ["python_repl"])
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("120", events[-1]["content"])
        self.assertIn("\u5982\u4f55\u8ba2\u8d2d", events[-1]["content"])

    async def test_tool_streaming_uses_incremental_text_instead_of_repeating_snapshot(self) -> None:
        fake_agent = FakeAgent(
            [
                (
                    "messages",
                    (
                        SimpleNamespace(content="\u73b0\u5728\u8ba9\u6211\u8bfb\u53d6\u8fd9\u4e24\u4efd\u62a5\u544a\u7684\u5185\u5bb9\uff1a"),
                        {"langgraph_node": "model"},
                    ),
                ),
                (
                    "messages",
                    (
                        SimpleNamespace(
                            content="\u73b0\u5728\u8ba9\u6211\u8bfb\u53d6\u8fd9\u4e24\u4efd\u62a5\u544a\u7684\u5185\u5bb9\uff1a\n1. \u4e09\u4e00\u91cd\u5de5"
                        ),
                        {"langgraph_node": "model"},
                    ),
                ),
                (
                    "messages",
                    (
                        SimpleNamespace(
                            content="\u73b0\u5728\u8ba9\u6211\u8bfb\u53d6\u8fd9\u4e24\u4efd\u62a5\u544a\u7684\u5185\u5bb9\uff1a\n1. \u4e09\u4e00\u91cd\u5de5\n2. \u822a\u5929\u52a8\u529b"
                        ),
                        {"langgraph_node": "model"},
                    ),
                ),
            ]
        )

        support = FakeExecutionSupport(tool_agent=fake_agent)
        with patch.object(self.manager, "create_execution_support", return_value=support):
            events = await collect_events(
                self.manager,
                "\u8bf7\u53ea\u7528 terminal \u8bfb\u53d6\u8fd9\u4e24\u4efd\u62a5\u544a\u7684\u5185\u5bb9\u3002",
            )

        token_events = [event["content"] for event in events if event["type"] == "token"]
        self.assertEqual(
            token_events,
            [
                "\u73b0\u5728\u8ba9\u6211\u8bfb\u53d6\u8fd9\u4e24\u4efd\u62a5\u544a\u7684\u5185\u5bb9\uff1a",
                "\n1. \u4e09\u4e00\u91cd\u5de5",
                "\n2. \u822a\u5929\u52a8\u529b",
            ],
        )
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(
            events[-1]["content"],
            "\u73b0\u5728\u8ba9\u6211\u8bfb\u53d6\u8fd9\u4e24\u4efd\u62a5\u544a\u7684\u5185\u5bb9\uff1a\n1. \u4e09\u4e00\u91cd\u5de5\n2. \u822a\u5929\u52a8\u529b",
        )


if __name__ == "__main__":
    unittest.main()
