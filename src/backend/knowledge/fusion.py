from __future__ import annotations

from typing import Iterable

from src.backend.knowledge.types import Evidence


def evidence_dedupe_key(item: Evidence) -> str:
    normalized_snippet = " ".join(item.snippet.split())
    return f"{item.source_path}|{item.locator}|{item.parent_id}|{normalized_snippet[:240]}"


def reciprocal_rank_fusion(
    evidence_lists: Iterable[list[Evidence]],
    *,
    top_k: int = 6,
    rank_constant: int = 60,
) -> list[Evidence]:
    scores: dict[str, float] = {}
    representatives: dict[str, Evidence] = {}

    for evidence_list in evidence_lists:
        for rank, evidence in enumerate(evidence_list, start=1):
            key = evidence_dedupe_key(evidence)
            scores[key] = scores.get(key, 0.0) + (1.0 / (rank_constant + rank))
            if key not in representatives:
                representatives[key] = evidence

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    fused: list[Evidence] = []
    for key, score in ranked[:top_k]:
        evidence = representatives[key]
        fused.append(
            Evidence(
                source_path=evidence.source_path,
                source_type=evidence.source_type,
                locator=evidence.locator,
                snippet=evidence.snippet,
                channel="fused",
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
    return fused
