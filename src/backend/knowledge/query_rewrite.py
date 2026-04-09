from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from src.backend.runtime.config import get_settings


QUESTION_TYPE_PATTERNS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "cross_file_aggregation",
        (
            re.compile(r"(横向比较|综合|汇总聚合|哪些.+来源路径|哪些.+财报路径|多份.+财报)"),
            re.compile(r"(across files|cross file|aggregate)", re.IGNORECASE),
        ),
    ),
    (
        "compare",
        (
            re.compile(r"(对比|比较|差异|分别|谁更|高于|低于)"),
            re.compile(r"\b(compare|versus|vs)\b", re.IGNORECASE),
        ),
    ),
    (
        "multi_hop",
        (
            re.compile(r"(同时|并且|以及|原因|关联|结合|既.+又)"),
            re.compile(r"\b(and|both|reason|because|together)\b", re.IGNORECASE),
        ),
    ),
    (
        "negation",
        (
            re.compile(r"(并未|不是|没有|未盈利|亏损|为负|负值)"),
            re.compile(r"\b(not|negative|loss|unprofitable)\b", re.IGNORECASE),
        ),
    ),
    (
        "fuzzy",
        (
            re.compile(r"(那个|哪份|哪张|哪个|大概|更像|类似|通俗|概括)"),
            re.compile(r"\b(which|roughly|kind of|similar to)\b", re.IGNORECASE),
        ),
    ),
)

FINANCIAL_ALIAS_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("营收", "营业收入", "营业总收入", "收入"), ("营业收入", "营业总收入", "收入", "营收")),
    (("净利润", "归母净利润", "归属于上市公司股东的净利润"), ("净利润", "归属于上市公司股东的净利润", "归母净利润")),
    (("亏损", "未盈利", "为负", "负值"), ("亏损", "未盈利", "净利润为负", "利润为负")),
    (("同比", "同比增长", "同比下降"), ("同比", "同比增长", "同比下降")),
    (("财报", "报告", "年报", "季报", "Q3", "三季度", "前三季度"), ("财报", "第三季度报告", "Q3", "前三季度")),
)

ENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "上汽集团": ("上汽集团", "上海汽车集团股份有限公司"),
    "三一重工": ("三一重工",),
    "航天动力": ("航天动力", "陕西航天动力高科技股份有限公司"),
    "OpenAI": ("OpenAI",),
    "ChatGPT": ("ChatGPT",),
    "Claude": ("Claude",),
}

STOP_TERMS = {
    "根据知识库",
    "知识库",
    "根据",
    "哪份",
    "哪张",
    "哪个",
    "那个",
    "请给出",
    "给出",
    "来源",
    "来源路径",
    "路径",
    "概括",
    "说明",
    "对比",
    "比较",
    "检索",
    "哪些",
    "文件",
    "文档",
    "报告",
}
EXCLUDED_ENTITY_FRAGMENTS = ("如果", "根据知识库", "对比", "比较", "横向比较", "来源路径", "三家公司", "哪些", "路径", "文档")
QUESTION_TYPES = {"cross_file_aggregation", "compare", "multi_hop", "negation", "fuzzy", "direct_fact"}

PLANNER_GUIDE = """You are a retrieval rewrite and planning module for a grounded RAG system.
Your job is to preserve the user's intent while making retrieval easier.

When to rewrite:
- Rewrite only if it helps retrieval focus or disambiguation.
- Preserve entities, time periods, metrics, constraints, and negation.
- Do not add facts or assumptions not in the original query.

Question type guide:
- compare: explicit comparison between entities or values.
- cross_file_aggregation: needs coverage across multiple files/families/entities.
- multi_hop: requires satisfying multiple constraints together.
- negation: asks whether something did not happen / was not true / was negative / loss-making.
- fuzzy: document-seeking, vague source lookup, or approximate report lookup.
- direct_fact: everything else.

Return JSON only with keys:
- question_type
- rewrite_needed
- query_variants
- entity_hints
- keyword_hints
- planner_reason
"""


@dataclass(frozen=True)
class QueryPlan:
    original_query: str
    question_type: str
    query_variants: list[str] = field(default_factory=list)
    entity_hints: list[str] = field(default_factory=list)
    keyword_hints: list[str] = field(default_factory=list)
    rewrite_needed: bool = False
    planner_reason: str = ""
    planner_source: str = "deterministic"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        candidate = str(value).strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            output.append(candidate)
    return output


def _detect_question_type(query: str) -> str:
    for question_type, patterns in QUESTION_TYPE_PATTERNS:
        if any(pattern.search(query) for pattern in patterns):
            return question_type
    return "direct_fact"


def _extract_entities(query: str) -> list[str]:
    entities: list[str] = []
    lowered_query = query.lower()
    for canonical, aliases in ENTITY_ALIASES.items():
        if any(alias.lower() in lowered_query for alias in aliases):
            entities.append(canonical)

    chinese_entities = re.findall(r"[\u4e00-\u9fff]{2,12}(?:集团|重工|动力|公司|科技|汽车|报告)", query)
    for candidate in chinese_entities:
        if any(fragment in candidate for fragment in EXCLUDED_ENTITY_FRAGMENTS):
            continue
        if "、" in candidate or "和" in candidate:
            continue
        entities.append(candidate)
    english_entities = re.findall(r"\b[A-Z][A-Za-z0-9.+-]{1,30}\b", query)
    for candidate in english_entities:
        if re.fullmatch(r"Q[1-4]", candidate, re.IGNORECASE):
            continue
        entities.append(candidate)
    return _dedupe(entities)


def _extract_keyword_hints(query: str) -> list[str]:
    hints: list[str] = []
    lowered_query = query.lower()
    for triggers, expansions in FINANCIAL_ALIAS_GROUPS:
        if any(trigger.lower() in lowered_query for trigger in triggers):
            hints.extend(expansions)

    time_hints = re.findall(r"(20\d{2}|Q[1-4]|前三季度|第三季度|年初至报告期末|本报告期)", query, flags=re.IGNORECASE)
    hints.extend(time_hints)

    salient_chinese = re.findall(r"[\u4e00-\u9fff]{2,12}", query)
    salient_english = re.findall(r"\b[A-Za-z][A-Za-z0-9.+-]{2,30}\b", query)
    for token in list(salient_chinese) + list(salient_english):
        if token not in STOP_TERMS:
            hints.append(token)
    return _dedupe(hints)


def _canonical_rewrite(entities: list[str], keyword_hints: list[str]) -> str:
    pieces = entities[:4] + keyword_hints[:6]
    return " ".join(_dedupe(pieces))


def _deterministic_query_plan(query: str) -> QueryPlan:
    normalized_query = str(query).strip()
    question_type = _detect_question_type(normalized_query)
    entities = _extract_entities(normalized_query)
    keyword_hints = _extract_keyword_hints(normalized_query)

    rewrites: list[str] = []
    canonical = _canonical_rewrite(entities, keyword_hints)
    if canonical and canonical.lower() != normalized_query.lower():
        rewrites.append(canonical)

    if question_type == "compare" and entities:
        compare_keywords = " ".join(keyword_hints[:4]) or "对比 财务表现"
        rewrites.extend(f"{entity} {compare_keywords}".strip() for entity in entities[:4])
    elif question_type == "cross_file_aggregation" and entities:
        aggregate_keywords = " ".join(keyword_hints[:5]) or "财报 业绩 对比"
        rewrites.extend(f"{entity} {aggregate_keywords}".strip() for entity in entities[:4])
        rewrites.append(" ".join(_dedupe(entities[:5] + keyword_hints[:5])))
    elif question_type == "negation":
        negative_keywords = [hint for hint in keyword_hints if hint in {"亏损", "未盈利", "净利润为负", "利润为负"}]
        positive_keywords = [hint for hint in keyword_hints if hint not in negative_keywords]
        if entities:
            rewrites.append(" ".join(_dedupe(entities[:3] + negative_keywords[:3] + positive_keywords[:3])))
            rewrites.append(" ".join(_dedupe(entities[:3] + ["净利润", "利润总额"] + positive_keywords[:3])))
    elif question_type == "multi_hop":
        bridge_terms = [hint for hint in keyword_hints if hint not in {"财报", "第三季度报告"}]
        if entities:
            rewrites.append(" ".join(_dedupe(entities[:3] + bridge_terms[:6])))
        rewrites.append(" ".join(_dedupe(keyword_hints[:8])))
    elif question_type == "fuzzy":
        if entities or keyword_hints:
            rewrites.append(" ".join(_dedupe(entities[:3] + keyword_hints[:6])))
        if "AI" in normalized_query or "OpenAI" in normalized_query:
            rewrites.append(" ".join(_dedupe(entities[:3] + ["报告", "应用", "营收", "员工人数"])))

    query_variants = [normalized_query]
    query_variants.extend(rewrites)
    query_variants = _dedupe(query_variants)[:5]

    return QueryPlan(
        original_query=normalized_query,
        question_type=question_type,
        query_variants=query_variants,
        entity_hints=entities,
        keyword_hints=keyword_hints,
        rewrite_needed=len(query_variants) > 1,
        planner_reason="deterministic fallback plan",
        planner_source="deterministic",
    )


def _extract_json_block(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if not text:
        raise ValueError("planner returned empty content")
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise ValueError("planner did not return JSON")
    return json.loads(text[first : last + 1])


def _normalize_question_type(value: str, fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in QUESTION_TYPES:
        return normalized
    return fallback


class LLMRewritePlanner:
    def __init__(self) -> None:
        self._model = None

    def _build_model(self):
        if self._model is not None:
            return self._model
        settings = get_settings()
        if not settings.llm_api_key:
            raise RuntimeError("Missing API key for rewrite planner")
        from langchain_openai import ChatOpenAI  # pylint: disable=import-outside-toplevel

        kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "api_key": settings.llm_api_key,
            "base_url": settings.llm_base_url,
            "temperature": 0.2,
            "max_tokens": 320,
        }
        if settings.llm_model == "kimi-k2.5" and settings.llm_thinking_type:
            kwargs["extra_body"] = {"thinking": {"type": settings.llm_thinking_type}}
            if settings.llm_thinking_type == "disabled":
                kwargs["temperature"] = None
        self._model = ChatOpenAI(**kwargs)
        return self._model

    def _prompt(self, query: str, deterministic_plan: QueryPlan) -> list[dict[str, str]]:
        user = {
            "original_query": query,
            "deterministic_question_type_hint": deterministic_plan.question_type,
            "deterministic_entity_hints": deterministic_plan.entity_hints,
            "deterministic_keyword_hints": deterministic_plan.keyword_hints,
            "deterministic_query_variants": deterministic_plan.query_variants[:4],
        }
        return [
            {"role": "system", "content": PLANNER_GUIDE},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False, indent=2)},
        ]

    def _parse(self, raw: str, query: str, deterministic_plan: QueryPlan) -> QueryPlan:
        payload = _extract_json_block(raw)
        question_type = _normalize_question_type(payload.get("question_type", ""), deterministic_plan.question_type)
        query_variants = _dedupe([str(item) for item in payload.get("query_variants", []) or [] if str(item).strip()])
        if query not in query_variants:
            query_variants.insert(0, query)
        if not query_variants:
            query_variants = deterministic_plan.query_variants
        entity_hints = _dedupe([str(item) for item in payload.get("entity_hints", []) or [] if str(item).strip()])
        keyword_hints = _dedupe([str(item) for item in payload.get("keyword_hints", []) or [] if str(item).strip()])
        rewrite_needed = bool(payload.get("rewrite_needed", False) or len(query_variants) > 1)
        planner_reason = str(payload.get("planner_reason", "") or "").strip()[:240] or "llm rewrite planner"
        return QueryPlan(
            original_query=str(query).strip(),
            question_type=question_type,
            query_variants=query_variants[:5],
            entity_hints=entity_hints[:8] or deterministic_plan.entity_hints,
            keyword_hints=keyword_hints[:10] or deterministic_plan.keyword_hints,
            rewrite_needed=rewrite_needed,
            planner_reason=planner_reason,
            planner_source="llm",
        )

    def plan(self, query: str) -> QueryPlan:
        deterministic_plan = _deterministic_query_plan(query)
        response = self._build_model().invoke(self._prompt(query, deterministic_plan))
        raw = str(getattr(response, "content", "") or "")
        return self._parse(raw, query, deterministic_plan)


_LLM_REWRITE_PLANNER = LLMRewritePlanner()


def build_query_plan(query: str, *, prefer_llm: bool = False) -> QueryPlan:
    if prefer_llm:
        try:
            return _LLM_REWRITE_PLANNER.plan(query)
        except Exception:
            pass
    return _deterministic_query_plan(query)
