from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.backend.decision.execution_strategy import parse_execution_strategy
from src.backend.decision.lightweight_router import RoutingDecision, deterministic_route
from src.backend.runtime.agent_manager import AgentManager


class LightweightRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.manager = AgentManager()
        self.manager.initialize(BACKEND_DIR)

    def test_deterministic_route_honors_tool_whitelist(self) -> None:
        strategy = parse_execution_strategy("Only use python_repl. Do not use any other tools.")
        decision = deterministic_route(
            message="Only use python_repl. Do not use any other tools.",
            strategy=strategy,
            tool_names=("fetch_url", "python_repl", "read_file", "terminal"),
            is_knowledge_query=False,
            prefer_tool_agent=True,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.intent, "computation_or_transformation")
        self.assertEqual(decision.allowed_tools, ("python_repl",))
        self.assertEqual(decision.subtype, "code_execution_request")

    def test_known_file_prefers_read_file_only(self) -> None:
        strategy = parse_execution_strategy("Read src/backend/runtime/config.py and summarize router_model.")
        decision = deterministic_route(
            message="Read src/backend/runtime/config.py and summarize router_model.",
            strategy=strategy,
            tool_names=("fetch_url", "python_repl", "read_file", "terminal"),
            is_knowledge_query=False,
            prefer_tool_agent=True,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.intent, "workspace_file_ops")
        self.assertEqual(decision.subtype, "read_existing_file")
        self.assertEqual(decision.allowed_tools, ("read_file",))

    def test_workspace_search_prefers_terminal_only(self) -> None:
        strategy = parse_execution_strategy("Find all markdown files under backend/tests.")
        decision = deterministic_route(
            message="Find all markdown files under backend/tests.",
            strategy=strategy,
            tool_names=("fetch_url", "python_repl", "read_file", "terminal"),
            is_knowledge_query=False,
            prefer_tool_agent=True,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.intent, "workspace_file_ops")
        self.assertEqual(decision.subtype, "search_workspace_file")
        self.assertEqual(decision.allowed_tools, ("terminal",))

    def test_explicit_filesystem_mcp_read_routes_without_llm(self) -> None:
        strategy = parse_execution_strategy(
            "Use Filesystem MCP only, read mcp_manual/read_me.txt, and tell me the exact content."
        )
        decision = deterministic_route(
            message="Use Filesystem MCP only, read mcp_manual/read_me.txt, and tell me the exact content.",
            strategy=strategy,
            tool_names=("fetch_url", "python_repl", "read_file", "terminal", "mcp_filesystem_read_file", "mcp_filesystem_list_directory"),
            is_knowledge_query=False,
            prefer_tool_agent=True,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.intent, "workspace_file_ops")
        self.assertEqual(decision.subtype, "read_existing_file")
        self.assertEqual(decision.allowed_tools, ("mcp_filesystem_read_file",))
        self.assertEqual(decision.source, "rules")

    def test_file_backed_calculation_prefers_python_only(self) -> None:
        strategy = parse_execution_strategy("Count how many rows are in knowledge/E-commerce Data/sales_orders.xlsx.")
        decision = deterministic_route(
            message="Count how many rows are in knowledge/E-commerce Data/sales_orders.xlsx.",
            strategy=strategy,
            tool_names=("fetch_url", "python_repl", "read_file", "terminal"),
            is_knowledge_query=False,
            prefer_tool_agent=True,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.intent, "computation_or_transformation")
        self.assertEqual(decision.subtype, "file_backed_calculation")
        self.assertEqual(decision.allowed_tools, ("python_repl",))

    def test_text_transformation_stays_direct(self) -> None:
        strategy = parse_execution_strategy("Rewrite this paragraph in a shorter style.")
        decision = deterministic_route(
            message="Rewrite this paragraph in a shorter style.",
            strategy=strategy,
            tool_names=("fetch_url", "python_repl", "read_file", "terminal"),
            is_knowledge_query=False,
            prefer_tool_agent=False,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.intent, "direct_answer")
        self.assertEqual(decision.subtype, "pure_text_transformation")
        self.assertEqual(decision.allowed_tools, ())

    def test_fuzzy_doc_seeking_now_defers_to_llm_router(self) -> None:
        strategy = parse_execution_strategy("I want that healthcare report and its source path.")
        decision = deterministic_route(
            message="I want that healthcare report and its source path.",
            strategy=strategy,
            tool_names=("fetch_url", "python_repl", "read_file", "terminal"),
            is_knowledge_query=True,
            prefer_tool_agent=False,
        )
        self.assertIsNone(decision)

    async def test_resolve_routing_skips_llm_when_rules_are_clear(self) -> None:
        with patch.object(self.manager._lightweight_router, "route", new_callable=AsyncMock) as mocked_route:
            _strategy, decision = await self.manager.resolve_routing(
                "Answer directly without tools or retrieval: explain the difference between RAG and fine-tuning.",
                [],
            )
        mocked_route.assert_not_awaited()
        self.assertEqual(decision.intent, "direct_answer")
        self.assertEqual(decision.allowed_tools, ())

    async def test_resolve_routing_skips_llm_for_explicit_filesystem_mcp(self) -> None:
        with patch.object(self.manager._lightweight_router, "route", new_callable=AsyncMock) as mocked_route:
            _strategy, decision = await self.manager.resolve_routing(
                "Use Filesystem MCP only, read mcp_manual/read_me.txt, and tell me the exact content.",
                [],
            )
        mocked_route.assert_not_awaited()
        self.assertEqual(decision.intent, "workspace_file_ops")
        self.assertEqual(decision.allowed_tools, ("mcp_filesystem_read_file",))
        self.assertEqual(decision.source, "rules")

    async def test_resolve_routing_uses_llm_for_ambiguous_message(self) -> None:
        mocked_decision = RoutingDecision(
            intent="knowledge_qa",
            needs_tools=False,
            needs_retrieval=True,
            allowed_tools=(),
            confidence=0.72,
            reason_short="document-seeking query",
            source="llm_router",
            prompt_tokens=55,
            output_tokens=22,
        )
        with patch.object(self.manager._lightweight_router, "route", new=AsyncMock(return_value=mocked_decision)):
            _strategy, decision = await self.manager.resolve_routing(
                "I want that refund-rules material, but I'm not sure which route it should take.",
                [],
            )
        self.assertEqual(decision.intent, "knowledge_qa")
        self.assertTrue(decision.needs_retrieval)
        self.assertEqual(decision.source, "llm_router")

    async def test_constraints_filter_router_tool_choice(self) -> None:
        mocked_decision = RoutingDecision(
            intent="workspace_file_ops",
            needs_tools=True,
            needs_retrieval=False,
            allowed_tools=("read_file", "terminal"),
            confidence=0.66,
            reason_short="workspace path request",
            source="llm_router",
            subtype="search_workspace_file",
        )
        with patch.object(self.manager._lightweight_router, "route", new=AsyncMock(return_value=mocked_decision)):
            _strategy, decision = await self.manager.resolve_routing(
                "Only use terminal to list files under knowledge/Financial Report Data.",
                [],
            )
        self.assertEqual(decision.intent, "workspace_file_ops")
        self.assertEqual(decision.allowed_tools, ("terminal",))

    async def test_ambiguous_router_prefers_large_model_contract(self) -> None:
        large_decision = RoutingDecision(
            intent="knowledge_qa",
            needs_tools=False,
            needs_retrieval=True,
            allowed_tools=(),
            confidence=0.81,
            reason_short="document seeking request",
            source="llm_router",
            prompt_tokens=60,
            output_tokens=30,
            model_name="kimi-k2.5",
        )

        with (
            patch.object(self.manager._lightweight_router, "_build_large_model", return_value=object()),
            patch.object(
                self.manager._lightweight_router,
                "_invoke_router",
                new=AsyncMock(return_value=large_decision),
            ),
        ):
            _strategy, decision = await self.manager.resolve_routing(
                "I want that medical AI report we talked about before.",
                [],
            )

        self.assertEqual(decision.intent, "knowledge_qa")
        self.assertFalse(decision.escalated)
        self.assertEqual(decision.source, "llm_router")


if __name__ == "__main__":
    unittest.main()

