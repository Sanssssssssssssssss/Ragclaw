from __future__ import annotations

import re
from collections import defaultdict

from knowledge_retrieval.types import Evidence


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


def merge_parent_evidences(
    evidences: list[Evidence],
    *,
    max_children_per_parent: int = 3,
    top_k: int = 10,
) -> list[Evidence]:
    grouped: dict[str, list[Evidence]] = defaultdict(list)
    for evidence in evidences:
        key = str(evidence.parent_id or f"{evidence.source_path}|{evidence.locator}").strip()
        grouped[key].append(evidence)

    merged: list[Evidence] = []
    for group in grouped.values():
        sorted_group = sorted(group, key=lambda item: float(item.score or 0.0), reverse=True)
        lead = sorted_group[0]
        selected_children = sorted_group[:max_children_per_parent]
        merged_snippet = "\n\n".join(
            snippet
            for snippet in [str(item.snippet or "").strip() for item in selected_children]
            if snippet
        )
        merged_locator = " | ".join(
            locator
            for locator in dict.fromkeys(str(item.locator or "").strip() for item in selected_children)
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

    max_per_source_family = 1 if question_type in {"compare", "cross_file_aggregation"} else 2
    def family_sort_key(item: Evidence) -> tuple[float, int]:
        path = str(item.source_path or "").lower()
        pdf_preference = 0
        if path.endswith(".pdf"):
            pdf_preference = 2
        elif path.endswith("_extracted.txt"):
            pdf_preference = 1
        return (float(item.score or 0.0), pdf_preference)

    family_best: dict[str, list[Evidence]] = defaultdict(list)
    for evidence in evidences:
        family_best[_source_family(evidence.source_path)].append(evidence)

    ordered: list[Evidence] = []
    for family, items in family_best.items():
        family_best[family] = sorted(items, key=family_sort_key, reverse=True)
        ordered.extend(family_best[family])

    ordered.sort(key=lambda item: (float(item.score or 0.0), 1 if str(item.source_path or "").lower().endswith(".pdf") else 0), reverse=True)
    counts: dict[str, int] = defaultdict(int)
    diversified: list[Evidence] = []
    deferred: list[Evidence] = []

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
        if counts[family] < max_per_source_family:
            diversified.append(evidence)
            counts[family] += 1
        else:
            deferred.append(evidence)
        if len(diversified) >= top_k:
            return diversified

    for evidence in deferred:
        diversified.append(evidence)
        if len(diversified) >= top_k:
            break
    return diversified
