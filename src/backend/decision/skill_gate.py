from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from src.backend.capabilities.registry import skill_capability_specs
from src.backend.runtime.config import get_settings


@dataclass(frozen=True)
class SkillProfile:
    capability_id: str
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
            "capability_id": self.capability_id,
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


_SKILL_NAME_OVERRIDES = {
    "skill.get_weather": "get_weather",
    "skill.kb_retriever": "kb-retriever",
    "skill.retry_lesson_capture": "retry-lesson-capture",
    "skill.web_search": "web-search",
}


def _skill_inventory() -> tuple[SkillProfile, ...]:
    profiles: list[SkillProfile] = []
    for spec in skill_capability_specs():
        skill_name = _SKILL_NAME_OVERRIDES.get(spec.capability_id, spec.capability_id.removeprefix("skill."))
        profiles.append(
            SkillProfile(
                capability_id=spec.capability_id,
                skill_name=skill_name,
                good_for=spec.when_to_use,
                bad_for=spec.when_not_to_use,
                requires_retrieval="knowledge" in spec.tags or "retrieval" in spec.tags,
                requires_tool_use=bool(spec.required_capabilities),
                risk_level=spec.risk_level,
                enabled=spec.enabled,
                required_tools=tuple(spec.required_capabilities),
            )
        )
    return tuple(profiles)


SKILL_INVENTORY = _skill_inventory()

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


def _extract_json_block(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise ValueError("skill selector did not return JSON")
    return json.loads(text[first : last + 1])


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
    def __init__(self) -> None:
        self._model = None

    def inventory(self) -> list[dict[str, Any]]:
        return skill_inventory()

    def _build_model(self):
        if self._model is not None:
            return self._model
        settings = get_settings()
        if not settings.llm_api_key:
            raise RuntimeError("Missing LLM API key for skill selector")
        from langchain_openai import ChatOpenAI  # pylint: disable=import-outside-toplevel

        kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "api_key": settings.llm_api_key,
            "base_url": settings.llm_base_url,
            "temperature": 0.2,
            "max_tokens": 120,
        }
        if settings.llm_model == "kimi-k2.5" and settings.llm_thinking_type:
            kwargs["extra_body"] = {"thinking": {"type": settings.llm_thinking_type}}
            if settings.llm_thinking_type == "disabled":
                kwargs["temperature"] = None
        self._model = ChatOpenAI(**kwargs)
        return self._model

    def _llm_skill_decision(
        self,
        *,
        message: str,
        history: list[dict[str, Any]],
        allowed_tools: tuple[str, ...],
    ) -> SkillDecision:
        cards = "\n".join(skill_prompt_cards()) or "none"
        prompt = [
            {
                "role": "system",
                "content": (
                    "You decide whether a request should use a specialized skill. "
                    "Return JSON only with keys: use_skill, skill_name, confidence, reason_short. "
                    "Allowed skill_name values: '', get_weather, web-search. "
                    "Prefer no skill unless a skill is clearly better than plain tools. "
                    "Never select a skill that needs tools outside the allowed tool list."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "message": message,
                        "recent_history": [str(item.get("content", "") or "")[:180] for item in history[-2:]],
                        "allowed_tools": list(allowed_tools),
                        "skills": cards,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]
        response = self._build_model().invoke(prompt)
        payload = _extract_json_block(str(getattr(response, "content", "") or ""))
        use_skill = bool(payload.get("use_skill", False))
        skill_name = str(payload.get("skill_name", "") or "").strip()
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        reason_short = str(payload.get("reason_short", "") or "").strip()[:120] or "llm skill selector"
        if not use_skill:
            return SkillDecision(False, "", confidence, reason_short)
        profile = INVENTORY_BY_NAME.get(skill_name)
        if profile is None or not profile.enabled or not _has_required_tools(profile.required_tools, allowed_tools):
            return SkillDecision(False, "", max(0.0, min(confidence, 0.4)), "skill rejected by rails")
        return SkillDecision(True, skill_name, max(0.0, min(confidence, 1.0)), reason_short)

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

        try:
            llm_decision = self._llm_skill_decision(
                message=normalized,
                history=history,
                allowed_tools=allowed_tools,
            )
            if llm_decision.use_skill:
                return llm_decision
        except Exception:
            pass

        weather_profile = INVENTORY_BY_NAME["get_weather"]
        if weather_profile.enabled and _is_weather_request(normalized) and _has_required_tools(weather_profile.required_tools, allowed_tools):
            return SkillDecision(True, weather_profile.skill_name, 0.78, "weather heuristic fallback")

        web_profile = INVENTORY_BY_NAME["web-search"]
        if web_profile.enabled and _is_web_search_request(normalized) and _has_required_tools(web_profile.required_tools, allowed_tools):
            return SkillDecision(True, web_profile.skill_name, 0.74, "web heuristic fallback")

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
