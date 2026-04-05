from __future__ import annotations

import re
from collections import defaultdict

from src.backend.knowledge.types import Evidence


def _normalize_source_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    if "knowledge/" in normalized and not normalized.startswith("knowledge/"):
        normalized = normalized[normalized.index("knowledge/") :]
    return normalized


def _source_family(path: str) -> str:
    normalized = _normalize_source_path(path)
    if not normalized:
        return normalized
    parent, _, filename = normalized.rpartition("/")
    lowered = filename.lower()
    if lowered.endswith("_extracted.txt"):
        stem = filename[: -len("_extracted.txt")]
        extension = ".pdf"
    else:
        stem, dot, ext = filename.rpartition(".")
        if dot:
            extension = f".{ext}"
        else:
            stem = filename
            extension = ""
    if extension.lower() == ".txt" and re.search(r"20\d{2}[_\s-]*q[1-4]|20\d{2}.+(季度|q[1-4])", stem, re.IGNORECASE):
        stem = re.sub(r"[_\s-]*(提取文本|extracted)$", "", stem, flags=re.IGNORECASE)
        extension = ".pdf"
    if extension.lower() == ".pdf":
        stem = re.sub(r"[_\s]+", " ", stem).strip()
    family = f"{stem}{extension}"
    return f"{parent}/{family}" if parent else family


def source_family(path: str) -> str:
    return _source_family(path)


KEY_EVIDENCE_PATTERNS = (
    re.compile(r"(营收|营业收入|营业总收入|收入|净利润|利润总额|同比|增长|下降|亏损|盈利)", re.IGNORECASE),
    re.compile(r"(原因|影响|导致|由于|因为|索赔|费用|减值|现金流|资产|负债)", re.IGNORECASE),
    re.compile(r"(Q[1-4]|20\d{2}|季度|报告期|年初至报告期末|本报告期|前三季度)", re.IGNORECASE),
)


def _snippet_priority(item: Evidence) -> tuple[float, int, int]:
    snippet = str(item.snippet or "")
    text = " ".join([str(item.source_path or ""), str(item.locator or ""), snippet])
    keyword_hits = sum(1 for pattern in KEY_EVIDENCE_PATTERNS if pattern.search(text))
    pdf_bonus = 1 if str(item.source_path or "").lower().endswith(".pdf") else 0
    return (float(item.score or 0.0), keyword_hits, pdf_bonus)


def _trim_text_to_budget(text: str, budget: int) -> str:
    clean = str(text or "").strip()
    if len(clean) <= budget:
        return clean
    return clean[: max(0, budget - 3)].rstrip() + "..."


def _constraint_signature(evidence: Evidence) -> tuple[str, ...]:
    text = " ".join(
        [
            str(evidence.source_path or ""),
            str(evidence.locator or ""),
            str(evidence.snippet or ""),
        ]
    ).lower()
    signature: list[str] = []
    if any(term in text for term in ("营收", "营业收入", "营业总收入", "收入", "净利润", "利润总额", "同比", "增长", "下降")):
        signature.append("performance")
    if any(term in text for term in ("原因", "影响", "导致", "由于", "因为", "索赔", "费用", "减值")):
        signature.append("reason")
    if any(term in text for term in ("q1", "q2", "q3", "q4", "季度", "报告期", "前三季度", "年初至报告期末", "本报告期")):
        signature.append("period")
    return tuple(signature) or ("general",)


def _selection_bonus(item: Evidence, question_type: str) -> tuple[int, int]:
    text = " ".join(
        [
            str(item.source_path or ""),
            str(item.locator or ""),
            str(item.snippet or ""),
        ]
    ).lower()
    numeric_bonus = 1 if re.search(r"-?\d[\d,]*(?:\.\d+)?", text) else 0
    if question_type == "negation":
        negative_bonus = 1 if any(term in text for term in ("亏损", "未盈利", "净利润为负", "利润为负", "负数", "-")) else 0
        metric_bonus = 1 if any(term in text for term in ("净利润", "利润总额")) else 0
        return (negative_bonus + metric_bonus, numeric_bonus)
    if question_type == "compare":
        compare_bonus = 1 if "%" in text else 0
        metric_bonus = 1 if any(term in text for term in ("净利润", "营业收入", "利润总额", "同比")) else 0
        return (compare_bonus + metric_bonus, numeric_bonus)
    if question_type == "multi_hop":
        reason_bonus = 1 if any(term in text for term in ("原因", "影响", "导致", "由于", "因为", "索赔", "费用")) else 0
        metric_bonus = 1 if any(term in text for term in ("净利润", "营业收入", "利润总额", "同比")) else 0
        return (reason_bonus + metric_bonus, numeric_bonus)
    return (0, numeric_bonus)


def _semantic_rank(item: Evidence, question_type: str) -> int:
    path = str(item.source_path or "").lower()
    chunk_type = str(item.chunk_type or "").strip().lower()
    normalized_chunk_type = "text" if chunk_type == "text-group" else chunk_type
    if path.endswith("data_structure.md"):
        return -3
    if normalized_chunk_type == "family_overview":
        return 1 if question_type in {"fuzzy", "cross_file_aggregation"} else -2
    if item.source_type == "pdf":
        if normalized_chunk_type == "table":
            return 4
        if normalized_chunk_type in {"text", "figure-caption"}:
            return 3
        return 2
    if path.endswith("_extracted.txt"):
        return 1
    if path.endswith(".txt"):
        return 0
    return 0


def _numeric_signal(text: str) -> int:
    return len(re.findall(r"-?\d[\d,]*(?:\.\d+)?%?", str(text or "")))


def merge_parent_evidences(
    evidences: list[Evidence],
    *,
    max_children_per_parent: int = 2,
    merged_snippet_total_chars: int = 1000,
    top_k: int = 10,
) -> list[Evidence]:
    grouped: dict[str, list[Evidence]] = defaultdict(list)
    for evidence in evidences:
        key = str(evidence.parent_id or f"{evidence.source_path}|{evidence.locator}").strip()
        grouped[key].append(evidence)

    merged: list[Evidence] = []
    for group in grouped.values():
        sorted_group = sorted(group, key=_snippet_priority, reverse=True)
        lead = sorted_group[0]
        selected_children = sorted_group[:max_children_per_parent]
        snippet_parts: list[str] = []
        remaining_budget = merged_snippet_total_chars
        for item in selected_children:
            snippet = str(item.snippet or "").strip()
            if not snippet or remaining_budget <= 0:
                continue
            trimmed = _trim_text_to_budget(snippet, remaining_budget)
            if not trimmed:
                continue
            snippet_parts.append(trimmed)
            remaining_budget -= len(trimmed)
            if remaining_budget > 2:
                remaining_budget -= 2
        merged_snippet = "\n\n".join(snippet_parts)
        merged_locator = " | ".join(
            locator
            for locator in list(dict.fromkeys(str(item.locator or "").strip() for item in selected_children))[:3]
            if locator
        )
        merged.append(
            Evidence(
                source_path=lead.source_path,
                source_type=lead.source_type,
                locator=merged_locator or lead.locator,
                snippet=merged_snippet or lead.snippet,
                channel=lead.channel,
                score=max(float(item.score or 0.0) for item in selected_children) + max(0, len(selected_children) - 1) * 0.15,
                parent_id=lead.parent_id,
                query_variant=lead.query_variant,
                supporting_children=len(selected_children),
                page=lead.page,
                bbox=lead.bbox,
                element_type=lead.element_type,
                section_title=lead.section_title,
                derived_json_path=lead.derived_json_path,
                derived_markdown_path=lead.derived_markdown_path,
                chunk_type=lead.chunk_type,
            )
        )

    merged.sort(key=lambda item: float(item.score or 0.0), reverse=True)
    return merged[:top_k]


def diversify_evidences(
    evidences: list[Evidence],
    *,
    question_type: str,
    entity_hints: list[str] | None = None,
    top_k: int = 6,
) -> list[Evidence]:
    if not evidences:
        return []

    max_per_source_family = 1

    def family_sort_key(item: Evidence) -> tuple[float, int, int, int]:
        semantic_preference = _semantic_rank(item, question_type)
        type_bonus, numeric_bonus = _selection_bonus(item, question_type)
        return (float(item.score or 0.0), semantic_preference, type_bonus, numeric_bonus)

    family_best: dict[str, list[Evidence]] = defaultdict(list)
    for evidence in evidences:
        family_best[_source_family(evidence.source_path)].append(evidence)

    ordered: list[Evidence] = []
    for family, items in family_best.items():
        family_best[family] = sorted(items, key=family_sort_key, reverse=True)
        ordered.extend(family_best[family])

    ordered.sort(
        key=lambda item: (
            float(item.score or 0.0),
            _semantic_rank(item, question_type),
            *_selection_bonus(item, question_type),
            1 if str(item.source_path or "").lower().endswith(".pdf") else 0,
        ),
        reverse=True,
    )
    counts: dict[str, int] = defaultdict(int)
    diversified: list[Evidence] = []
    deferred: list[Evidence] = []

    def has_same_family_semantic(target: Evidence) -> bool:
        family = _source_family(target.source_path)
        return any(
            _source_family(existing.source_path) == family
            and existing is not target
            and _semantic_rank(existing, question_type) >= 3
            for existing in ordered
        )

    if question_type in {"compare", "cross_file_aggregation"} and entity_hints:
        for entity in entity_hints:
            candidate = next(
                (
                    item
                    for item in ordered
                    if entity.lower() in f"{item.source_path} {item.snippet}".lower()
                    and counts[_source_family(item.source_path)] < max_per_source_family
                    and item not in diversified
                ),
                None,
            )
            if candidate is None:
                continue
            family = _source_family(candidate.source_path)
            diversified.append(candidate)
            counts[family] += 1
            if len(diversified) >= top_k:
                return diversified

    for evidence in ordered:
        if evidence in diversified:
            continue
        family = _source_family(evidence.source_path)
        lowered_path = str(evidence.source_path or "").lower()
        evidence_text = " ".join([str(evidence.locator or ""), str(evidence.snippet or "")])
        if (
            str(evidence.chunk_type or "").strip().lower() == "family_overview"
            and any(
                _source_family(existing.source_path) == family
                and str(existing.chunk_type or "").strip().lower() != "family_overview"
                for existing in ordered
            )
        ):
            deferred.append(evidence)
            continue
        if lowered_path.endswith("data_structure.md") and any(_semantic_rank(existing, question_type) >= 2 for existing in ordered):
            deferred.append(evidence)
            continue
        if question_type in {"compare", "negation", "multi_hop"} and lowered_path.endswith(".txt") and has_same_family_semantic(evidence):
            deferred.append(evidence)
            continue
        if question_type == "compare" and _numeric_signal(evidence_text) == 0 and has_same_family_semantic(evidence):
            deferred.append(evidence)
            continue
        if counts[family] < max_per_source_family:
            diversified.append(evidence)
            counts[family] += 1
        elif (
            question_type == "multi_hop"
            and counts[family] < 2
            and all(str(existing.parent_id or "") != str(evidence.parent_id or "") for existing in diversified if _source_family(existing.source_path) == family)
            and not any(
                set(_constraint_signature(existing)) == set(_constraint_signature(evidence))
                for existing in diversified
                if _source_family(existing.source_path) == family
            )
        ):
            diversified.append(evidence)
            counts[family] += 1
        else:
            deferred.append(evidence)
        if len(diversified) >= top_k:
            return diversified

    if question_type == "multi_hop":
        for evidence in deferred:
            diversified.append(evidence)
            if len(diversified) >= top_k:
                break
    return diversified
