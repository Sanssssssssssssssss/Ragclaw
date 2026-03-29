from __future__ import annotations

import re
from collections import Counter

from knowledge_retrieval.query_rewrite import QueryPlan
from knowledge_retrieval.types import Evidence


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


def rerank_evidences(
    query_plan: QueryPlan,
    candidates: list[Evidence],
    *,
    top_k: int = 10,
) -> list[Evidence]:
    original_tokens = _tokenize(query_plan.original_query)
    rewrite_tokens = _tokenize(" ".join(query_plan.query_variants[1:]))
    entity_tokens = _tokenize(" ".join(query_plan.entity_hints))
    hint_tokens = _tokenize(" ".join(query_plan.keyword_hints))

    ranked: list[tuple[float, Evidence]] = []
    for index, evidence in enumerate(candidates):
        candidate_text = _text_blob(evidence)
        candidate_tokens = _tokenize(candidate_text)
        score = float(evidence.score or 0.0)
        score += _overlap_score(original_tokens, candidate_tokens) * 0.28
        score += _overlap_score(rewrite_tokens, candidate_tokens) * 0.12
        score += _overlap_score(entity_tokens, candidate_tokens) * 0.35
        score += _overlap_score(hint_tokens, candidate_tokens) * 0.18

        snippet = str(evidence.snippet or "")
        source_path = str(evidence.source_path or "")
        lowered_path = source_path.lower()

        entity_path_hits = sum(entity.lower() in lowered_path for entity in query_plan.entity_hints)
        entity_text_hits = sum(entity.lower() in candidate_text.lower() for entity in query_plan.entity_hints)
        score += min(entity_path_hits, 3) * 0.9
        score += min(entity_text_hits, 4) * 0.22

        if query_plan.entity_hints and not entity_text_hits and query_plan.question_type in {"compare", "cross_file_aggregation", "multi_hop"}:
            score -= 0.4

        if query_plan.question_type == "negation" and any(term in snippet for term in NEGATIVE_TERMS):
            score += 1.0
        if query_plan.question_type == "compare" and any(term in query_plan.original_query for term in COMPARE_TERMS):
            score += min(entity_text_hits, 3) * 0.45
        if query_plan.question_type == "multi_hop" and any(term in snippet for term in MULTI_HOP_TERMS):
            score += 0.75
        if query_plan.question_type == "cross_file_aggregation":
            score += min(entity_path_hits, 3) * 0.6

        if lowered_path.endswith(".pdf"):
            score += 0.9
        if lowered_path.endswith("_extracted.txt"):
            score += 0.25
        elif lowered_path.endswith(".txt"):
            score -= 0.1
        if lowered_path.endswith("data_structure.md"):
            score -= 1.6 if query_plan.question_type in {"direct_fact", "fuzzy", "negation", "multi_hop"} else 0.8
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
            )
        )
    return reranked
