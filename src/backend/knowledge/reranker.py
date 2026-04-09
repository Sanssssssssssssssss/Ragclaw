from __future__ import annotations

import re
from collections import Counter

from src.backend.knowledge.evidence_organizer import source_family
from src.backend.knowledge.query_rewrite import QueryPlan
from src.backend.knowledge.types import Evidence


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")
NEGATIVE_TERMS = ("亏损", "未盈利", "净利润为负", "利润为负", "负值", "下降")
COMPARE_TERMS = ("对比", "比较", "差异", "高于", "低于", "分别")
MULTI_HOP_TERMS = ("同时", "原因", "由于", "关联", "以及", "损失", "增长")


def _tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_PATTERN.finditer(str(text or ""))]


def _text_blob(evidence: Evidence) -> str:
    return " ".join(
        [
            str(evidence.source_path or ""),
            str(evidence.locator or ""),
            str(evidence.section_title or ""),
            str(evidence.snippet or ""),
        ]
    )


def _overlap_score(query_tokens: list[str], candidate_tokens: list[str]) -> float:
    if not query_tokens or not candidate_tokens:
        return 0.0
    candidate_counts = Counter(candidate_tokens)
    score = 0.0
    for token in set(query_tokens):
        if token in candidate_counts:
            score += 1.0 + min(candidate_counts[token], 3) * 0.25
    return score


def _semantic_pdf_bonus(evidence: Evidence, question_type: str) -> float:
    path = str(evidence.source_path or "").lower()
    chunk_type = str(evidence.chunk_type or "").strip().lower()
    normalized_chunk_type = "text" if chunk_type == "text-group" else chunk_type

    if path.endswith("data_structure.md"):
        if question_type in {"compare", "negation", "multi_hop", "cross_file_aggregation"}:
            return -2.6
        if question_type == "fuzzy":
            return -1.9
        return -1.0

    if normalized_chunk_type == "family_overview":
        return 0.7 if question_type in {"fuzzy", "cross_file_aggregation"} else -0.8

    if evidence.source_type == "pdf":
        if normalized_chunk_type == "table":
            return 1.8 if question_type in {"compare", "negation", "multi_hop", "cross_file_aggregation"} else 1.2
        if normalized_chunk_type in {"text", "figure-caption"}:
            return 1.4 if question_type in {"compare", "negation", "multi_hop", "cross_file_aggregation"} else 1.0
        return 1.0

    if path.endswith("_extracted.txt"):
        return -0.35 if question_type in {"compare", "negation", "multi_hop", "cross_file_aggregation"} else -0.1
    if path.endswith(".txt"):
        return -0.45 if question_type in {"compare", "negation", "multi_hop", "cross_file_aggregation"} else -0.15
    return 0.0


def _numeric_signal_count(text: str) -> int:
    return len(re.findall(r"-?\d[\d,]*(?:\.\d+)?%?", str(text or "")))


def _constraint_coverage(text: str) -> set[str]:
    lowered = str(text or "").lower()
    coverage: set[str] = set()
    if any(term in lowered for term in ("营收", "营业收入", "收入", "净利润", "利润总额", "同比", "增长", "下降")):
        coverage.add("performance")
    if any(term in lowered for term in ("原因", "影响", "导致", "由于", "因为", "索赔", "费用", "减值")):
        coverage.add("reason")
    if any(term in lowered for term in ("q1", "q2", "q3", "q4", "季度", "本报告期", "前三季度", "年初至报告期末")):
        coverage.add("period")
    return coverage


def _locator_intent_bonus(evidence: Evidence, question_type: str) -> float:
    text = " ".join([str(evidence.locator or ""), str(evidence.section_title or ""), str(evidence.snippet or "")]).lower()
    bonus = 0.0
    if question_type in {"compare", "negation"}:
        if any(term in text for term in ("主要财务数据", "主要会计数据", "利润表", "合并利润表")):
            bonus += 0.9
        if any(term in text for term in ("非经常性损益", "被合并方", "同一控制下企业合并")):
            bonus -= 1.2
    if question_type == "multi_hop":
        if any(term in text for term in ("主要原因", "原因", "影响", "说明")):
            bonus += 0.5
        if any(term in text for term in ("主要财务数据", "利润表", "合并利润表")):
            bonus += 0.35
    return bonus


def rerank_evidences(
    query_plan: QueryPlan,
    candidates: list[Evidence],
    *,
    top_k: int = 10,
    preferred_families: list[str] | None = None,
) -> list[Evidence]:
    original_tokens = _tokenize(query_plan.original_query)
    rewrite_tokens = _tokenize(" ".join(query_plan.query_variants[1:]))
    entity_tokens = _tokenize(" ".join(query_plan.entity_hints))
    hint_tokens = _tokenize(" ".join(query_plan.keyword_hints))
    preferred_family_set = {source_family(item) for item in preferred_families or [] if str(item).strip()}
    family_has_pdf_semantic = {
        source_family(item.source_path): any(
            other.source_type == "pdf"
            and str(other.chunk_type or "").strip().lower() not in {"family_overview", ""}
            for other in candidates
            if source_family(other.source_path) == source_family(item.source_path)
        )
        for item in candidates
    }

    ranked: list[tuple[float, Evidence]] = []
    for index, evidence in enumerate(candidates):
        candidate_text = _text_blob(evidence)
        candidate_tokens = _tokenize(candidate_text)
        lowered_path = str(evidence.source_path or "").lower()
        evidence_family = source_family(evidence.source_path)
        digit_count = _numeric_signal_count(candidate_text)
        coverage = _constraint_coverage(candidate_text)

        score = float(evidence.score or 0.0)
        score += _overlap_score(original_tokens, candidate_tokens) * 0.28
        score += _overlap_score(rewrite_tokens, candidate_tokens) * 0.12
        score += _overlap_score(entity_tokens, candidate_tokens) * 0.35
        score += _overlap_score(hint_tokens, candidate_tokens) * 0.18

        entity_path_hits = sum(entity.lower() in lowered_path for entity in query_plan.entity_hints)
        entity_text_hits = sum(entity.lower() in candidate_text.lower() for entity in query_plan.entity_hints)
        score += min(entity_path_hits, 3) * 0.9
        score += min(entity_text_hits, 4) * 0.22

        if query_plan.entity_hints and not entity_text_hits and query_plan.question_type in {"compare", "cross_file_aggregation", "multi_hop"}:
            score -= 0.4

        if query_plan.question_type == "negation":
            if any(term in candidate_text for term in NEGATIVE_TERMS):
                score += 1.0
            elif digit_count >= 1:
                score -= 0.6
            else:
                score -= 0.45

        if query_plan.question_type == "compare":
            if any(term in query_plan.original_query for term in COMPARE_TERMS):
                score += min(entity_text_hits, 3) * 0.45
            if digit_count >= 2:
                score += 0.8
            elif digit_count == 0:
                score -= 0.9
            if "%" in candidate_text:
                score += 0.35

        if query_plan.question_type == "multi_hop":
            if any(term in candidate_text for term in MULTI_HOP_TERMS):
                score += 0.75
            score += len(coverage) * 0.45
            if {"performance", "reason"} <= coverage:
                score += 1.2
            elif "performance" in coverage or "reason" in coverage:
                score += 0.25
            if int(evidence.supporting_children or 1) >= 2:
                score += 0.3

        if query_plan.question_type == "cross_file_aggregation":
            score += min(entity_path_hits, 3) * 0.6

        score += _semantic_pdf_bonus(evidence, query_plan.question_type)
        score += _locator_intent_bonus(evidence, query_plan.question_type)

        if preferred_family_set:
            if evidence_family in preferred_family_set:
                score += 1.1
            elif query_plan.question_type in {"fuzzy", "cross_file_aggregation"}:
                score -= 0.5

        if (
            query_plan.question_type in {"compare", "negation", "multi_hop"}
            and lowered_path.endswith(".txt")
            and family_has_pdf_semantic.get(evidence_family)
        ):
            score -= 1.35

        if any(term in query_plan.original_query for term in ("PDF", "pdf", "财报", "报告", "来源路径")) and lowered_path.endswith(".pdf"):
            score += 0.75

        ranked.append((score - index * 0.001, evidence))

    ranked.sort(key=lambda item: item[0], reverse=True)
    reranked: list[Evidence] = []
    for score, evidence in ranked[:top_k]:
        reranked.append(
            Evidence(
                source_path=evidence.source_path,
                source_type=evidence.source_type,
                locator=evidence.locator,
                snippet=evidence.snippet,
                channel=evidence.channel,
                score=score,
                parent_id=evidence.parent_id,
                query_variant=evidence.query_variant,
                supporting_children=evidence.supporting_children,
                page=evidence.page,
                bbox=evidence.bbox,
                element_type=evidence.element_type,
                section_title=evidence.section_title,
                derived_json_path=evidence.derived_json_path,
                derived_markdown_path=evidence.derived_markdown_path,
                chunk_type=evidence.chunk_type,
            )
        )
    return reranked
