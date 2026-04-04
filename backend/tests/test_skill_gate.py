from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from graph.lightweight_router import RoutingDecision
from graph.execution_strategy import parse_execution_strategy
from graph.skill_gate import SkillDecision, SkillGate


class SkillGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = SkillGate()

    def test_inventory_contains_expected_skills(self) -> None:
        inventory = self.gate.inventory()
        names = {item["skill_name"] for item in inventory}
        self.assertEqual(names, {"get_weather", "kb-retriever", "retry-lesson-capture", "web-search"})

    def test_web_lookup_latest_prefers_web_search_skill(self) -> None:
        with patch.object(
            self.gate,
            "_llm_skill_decision",
            return_value=SkillDecision(True, "web-search", 0.93, "llm chose web-search"),
        ):
            decision = self.gate.decide(
                message="Look up the latest OpenAI pricing on the web and give me the official link.",
                history=[],
                strategy=parse_execution_strategy("Look up the latest OpenAI pricing on the web and give me the official link."),
                routing_decision=RoutingDecision(
                    intent="web_lookup",
                    needs_tools=True,
                    needs_retrieval=False,
                    allowed_tools=("fetch_url",),
                    confidence=0.9,
                    reason_short="clear web request",
                    source="rules",
                ),
            )
        self.assertTrue(decision.use_skill)
        self.assertEqual(decision.skill_name, "web-search")

    def test_weather_prefers_get_weather_skill(self) -> None:
        with patch.object(
            self.gate,
            "_llm_skill_decision",
            side_effect=RuntimeError("offline"),
        ):
            decision = self.gate.decide(
                message="Look up today's weather in London on the web.",
                history=[],
                strategy=parse_execution_strategy("Look up today's weather in London on the web."),
                routing_decision=RoutingDecision(
                    intent="web_lookup",
                    needs_tools=True,
                    needs_retrieval=False,
                    allowed_tools=("fetch_url",),
                    confidence=0.9,
                    reason_short="clear web request",
                    source="rules",
                ),
            )
        self.assertTrue(decision.use_skill)
        self.assertEqual(decision.skill_name, "get_weather")

    def test_knowledge_path_does_not_use_skill(self) -> None:
        decision = self.gate.decide(
            message="From the knowledge base, which report mentions AI healthcare applications? Return the path.",
            history=[],
            strategy=parse_execution_strategy("From the knowledge base, which report mentions AI healthcare applications? Return the path."),
            routing_decision=RoutingDecision(
                intent="knowledge_qa",
                needs_tools=False,
                needs_retrieval=True,
                allowed_tools=(),
                confidence=0.95,
                reason_short="clear knowledge request",
                source="rules",
            ),
        )
        self.assertFalse(decision.use_skill)

    def test_workspace_request_does_not_use_skill(self) -> None:
        decision = self.gate.decide(
            message="Read backend/config.py and tell me what ROUTER_MODEL is set to.",
            history=[],
            strategy=parse_execution_strategy("Read backend/config.py and tell me what ROUTER_MODEL is set to."),
            routing_decision=RoutingDecision(
                intent="workspace_file_ops",
                needs_tools=True,
                needs_retrieval=False,
                allowed_tools=("read_file",),
                confidence=0.9,
                reason_short="clear workspace operation",
                source="rules",
                subtype="read_existing_file",
            ),
        )
        self.assertFalse(decision.use_skill)

    def test_ambiguous_local_request_suppresses_skill(self) -> None:
        decision = self.gate.decide(
            message="Search the local workspace for that report and tell me where it is.",
            history=[],
            strategy=parse_execution_strategy("Search the local workspace for that report and tell me where it is."),
            routing_decision=RoutingDecision(
                intent="web_lookup",
                needs_tools=True,
                needs_retrieval=False,
                allowed_tools=("fetch_url",),
                confidence=0.6,
                reason_short="ambiguous route",
                source="llm_router",
                ambiguity_flags=("mixed_intent",),
            ),
        )
        self.assertFalse(decision.use_skill)


if __name__ == "__main__":
    unittest.main()
