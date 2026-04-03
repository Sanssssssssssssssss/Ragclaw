from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SkillProfile:
    skill_name: str
    good_for: str
    bad_for: str
    requires_retrieval: bool
    requires_tool_use: bool
    risk_level: str
    enabled: bool
    required_tools: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "good_for": self.good_for,
            "bad_for": self.bad_for,
            "requires_retrieval": self.requires_retrieval,
            "requires_tool_use": self.requires_tool_use,
            "risk_level": self.risk_level,
            "enabled": self.enabled,
            "required_tools": list(self.required_tools),
        }


@dataclass(frozen=True)
class SkillDecision:
    use_skill: bool
    skill_name: str
    confidence: float
    reason_short: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "use_skill": self.use_skill,
            "skill_name": self.skill_name,
            "confidence": self.confidence,
            "reason_short": self.reason_short,
        }


SKILL_INVENTORY = (
    SkillProfile(
        skill_name="get_weather",
        good_for="Explicit weather lookup by city or forecast window.",
        bad_for="General web research, local files, knowledge-base QA.",
        requires_retrieval=False,
        requires_tool_use=True,
        risk_level="medium",
        enabled=True,
        required_tools=("fetch_url",),
    ),
    SkillProfile(
        skill_name="kb-retriever",
        good_for="Legacy local knowledge-directory search workflows.",
        bad_for="Current formal knowledge QA path and normal document-seeking.",
        requires_retrieval=True,
        requires_tool_use=True,
        risk_level="high",
        enabled=False,
        required_tools=("read_file", "terminal", "python_repl"),
    ),
    SkillProfile(
        skill_name="retry-lesson-capture",
        good_for="Internal post-recovery lesson capture after a failure then success.",
        bad_for="Any user-facing request during normal execution.",
        requires_retrieval=False,
        requires_tool_use=True,
        risk_level="high",
        enabled=False,
        required_tools=("read_file", "python_repl"),
    ),
    SkillProfile(
        skill_name="web-search",
        good_for="Explicit latest/current online facts, official docs, links, news, pricing.",
        bad_for="Knowledge-base QA, workspace ops, direct explanations without web need.",
        requires_retrieval=False,
        requires_tool_use=True,
        risk_level="medium",
        enabled=True,
        required_tools=("fetch_url",),
    ),
)

INVENTORY_BY_NAME = {profile.skill_name: profile for profile in SKILL_INVENTORY}

WEATHER_PATTERNS = (
    re.compile(r"\b(weather|forecast|temperature|rain|wind)\b", re.IGNORECASE),
    re.compile(r"(天气|气温|预报|降雨|风速)"),
)
WEB_SEARCH_PATTERNS = (
    re.compile(r"\b(latest|current|official|docs?|documentation|news|pricing|homepage|link|search online|look up)\b", re.IGNORECASE),
    re.compile(r"(最新|当前|官网|文档|新闻|价格|主页|链接|联网搜索|在线查)"),
)
LOCAL_PATH_PATTERNS = (
    re.compile(r"(knowledge/|workspace/|backend/|memory/|storage/)", re.IGNORECASE),
    re.compile(r"\b(local|workspace|repo|repository|folder|directory)\b", re.IGNORECASE),
    re.compile(r"(本地|工作区|仓库|目录|文件夹)"),
)
KNOWLEDGE_PATTERNS = (
    re.compile(r"\bknowledge\b", re.IGNORECASE),
    re.compile(r"(知识库|根据知识库|从知识库)"),
)


def skill_inventory() -> list[dict[str, Any]]:
    return [profile.to_dict() for profile in SKILL_INVENTORY]


def skill_prompt_cards() -> list[str]:
    cards: list[str] = []
    for profile in SKILL_INVENTORY:
        if not profile.enabled:
            continue
        cards.append(
            f"- {profile.skill_name}: good for {profile.good_for} "
            f"Bad for {profile.bad_for} Requires tools: {', '.join(profile.required_tools) or 'none'}."
        )
    return cards


def _has_required_tools(required_tools: tuple[str, ...], allowed_tools: tuple[str, ...]) -> bool:
    if not required_tools:
        return True
    if not allowed_tools:
        return False
    allowed_set = set(allowed_tools)
    return set(required_tools).issubset(allowed_set)


def _is_weather_request(message: str) -> bool:
    normalized = str(message or "").strip()
    return any(pattern.search(normalized) for pattern in WEATHER_PATTERNS) or any(
        term in normalized for term in ("天气", "预报", "气温", "下雨", "降雨", "风力", "风速")
    )


def _is_web_search_request(message: str) -> bool:
    normalized = str(message or "").strip()
    return any(pattern.search(normalized) for pattern in WEB_SEARCH_PATTERNS) or any(
        term in normalized for term in ("最新", "当前", "官网", "文档", "新闻", "价格", "主页", "链接", "网上结果", "在线资料")
    )


def _is_localish_request(message: str, history: list[dict[str, Any]]) -> bool:
    normalized = str(message or "").strip()
    if any(pattern.search(normalized) for pattern in LOCAL_PATH_PATTERNS):
        return True
    history_text = " ".join(str(item.get("content", "") or "") for item in history[-2:])
    return any(pattern.search(history_text) for pattern in LOCAL_PATH_PATTERNS)


class SkillGate:
    def inventory(self) -> list[dict[str, Any]]:
        return skill_inventory()

    def decide(
        self,
        *,
        message: str,
        history: list[dict[str, Any]],
        strategy: Any,
        routing_decision: Any,
    ) -> SkillDecision:
        intent = str(getattr(routing_decision, "intent", "") or "").strip()
        allowed_tools = tuple(getattr(routing_decision, "allowed_tools", ()) or ())
        ambiguity_flags = tuple(getattr(routing_decision, "ambiguity_flags", ()) or ())
        normalized = str(message or "").strip()

        if getattr(strategy, "force_direct_answer", False):
            return SkillDecision(False, "", 0.0, "direct answer forced")
        if intent == "knowledge_qa":
            return SkillDecision(False, "", 0.02, "formal knowledge path owns QA")
        if intent == "workspace_file_ops":
            return SkillDecision(False, "", 0.03, "workspace ops stay on tools")
        if intent == "computation_or_transformation":
            return SkillDecision(False, "", 0.03, "computation stays on tools")
        if intent == "direct_answer":
            return SkillDecision(False, "", 0.01, "direct answer is sufficient")
        if intent != "web_lookup":
            return SkillDecision(False, "", 0.01, "route not skill-eligible")

        if _is_localish_request(normalized, history) or any(flag in {"mixed_intent", "context_dependent"} for flag in ambiguity_flags):
            return SkillDecision(False, "", 0.08, "keep fuzzy local requests off skills")

        if any(pattern.search(normalized) for pattern in KNOWLEDGE_PATTERNS):
            return SkillDecision(False, "", 0.08, "knowledge-looking request should not use skills")

        weather_profile = INVENTORY_BY_NAME["get_weather"]
        if (
            weather_profile.enabled
            and _is_weather_request(normalized)
            and _has_required_tools(weather_profile.required_tools, allowed_tools)
        ):
            return SkillDecision(True, weather_profile.skill_name, 0.92, "explicit weather lookup")

        web_profile = INVENTORY_BY_NAME["web-search"]
        if (
            web_profile.enabled
            and _is_web_search_request(normalized)
            and _has_required_tools(web_profile.required_tools, allowed_tools)
        ):
            return SkillDecision(True, web_profile.skill_name, 0.89, "explicit live web lookup")

        return SkillDecision(False, "", 0.12, "default to plain route and tools")


def skill_instruction(skill_name: str) -> list[str]:
    if skill_name == "web-search":
        return [
            "Use the local web-search skill workflow for this request.",
            "Use it only for live/current online facts, official docs, links, pricing, or news.",
            "Stay on web lookup only; do not switch into workspace or knowledge-base search.",
            "If the fetched result is partial, answer conservatively and surface the best links or sources you actually obtained.",
        ]
    if skill_name == "get_weather":
        return [
            "Use the local get_weather skill workflow for this request.",
            "Prefer a narrow weather lookup and answer with the requested city/forecast only.",
            "Do not turn this into a general web search task.",
        ]
    return []
