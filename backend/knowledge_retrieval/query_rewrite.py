from __future__ import annotations

import re
from dataclasses import dataclass, field


QUESTION_TYPE_PATTERNS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "cross_file_aggregation",
        (
            re.compile(r"(横向比较|综合|汇总|聚合|哪些.+来源路径|哪些.+财报路径|多份.+财报)"),
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
            re.compile(r"(同时|且|并且|以及|原因|关联|结合|既.+又)"),
            re.compile(r"\b(and|both|reason|because|together)\b", re.IGNORECASE),
        ),
    ),
    (
        "negation",
        (
            re.compile(r"(并未|不是|非|未盈利|亏损|为负|负值|没有)"),
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


@dataclass(frozen=True)
class QueryPlan:
    original_query: str
    question_type: str
    query_variants: list[str] = field(default_factory=list)
    entity_hints: list[str] = field(default_factory=list)
    keyword_hints: list[str] = field(default_factory=list)


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


def build_query_plan(query: str) -> QueryPlan:
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
    )
