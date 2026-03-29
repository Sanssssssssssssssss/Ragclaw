from __future__ import annotations

from knowledge_retrieval.query_rewrite import QueryPlan, build_query_plan
from knowledge_retrieval.indexer import knowledge_indexer
from knowledge_retrieval.types import Evidence, HybridRetrievalResult


def _dedupe_evidences(evidences: list[Evidence]) -> list[Evidence]:
    deduped: dict[str, Evidence] = {}
    for evidence in evidences:
        if not str(evidence.source_path or "").strip():
            continue
        key = "|".join(
            [
                str(evidence.source_path or "").strip(),
                str(evidence.locator or "").strip(),
                " ".join(str(evidence.snippet or "").split())[:240],
            ]
        )
        current = deduped.get(key)
        current_score = float(current.score or 0.0) if current is not None else float("-inf")
        next_score = float(evidence.score or 0.0)
        if current is None or next_score > current_score:
            deduped[key] = evidence
    return sorted(deduped.values(), key=lambda item: float(item.score or 0.0), reverse=True)


class HybridRetriever:
    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 4,
        path_filters: list[str] | None = None,
        query_hints: list[str] | None = None,
        query_plan: QueryPlan | None = None,
    ) -> HybridRetrievalResult:
        plan = query_plan or build_query_plan(query)
        variants = list(plan.query_variants) or [query]
        all_vector: list[Evidence] = []
        all_bm25: list[Evidence] = []

        for variant in variants:
            vector_hits = knowledge_indexer.retrieve_vector(
                variant,
                top_k=max(top_k * 2, top_k),
                path_filters=path_filters,
            )
            bm25_hits = knowledge_indexer.retrieve_bm25(
                variant,
                top_k=max(top_k * 2, top_k),
                path_filters=path_filters,
                query_hints=list(query_hints or []) + list(plan.keyword_hints) + list(plan.entity_hints),
            )

            all_vector.extend(
                Evidence(
                    source_path=item.source_path,
                    source_type=item.source_type,
                    locator=item.locator,
                    snippet=item.snippet,
                    channel=item.channel,
                    score=item.score,
                    parent_id=item.parent_id,
                    query_variant=variant,
                    supporting_children=item.supporting_children,
                )
                for item in vector_hits
            )
            all_bm25.extend(
                Evidence(
                    source_path=item.source_path,
                    source_type=item.source_type,
                    locator=item.locator,
                    snippet=item.snippet,
                    channel=item.channel,
                    score=item.score,
                    parent_id=item.parent_id,
                    query_variant=variant,
                    supporting_children=item.supporting_children,
                )
                for item in bm25_hits
            )

        return HybridRetrievalResult(
            vector_evidences=_dedupe_evidences(all_vector)[: max(top_k * 4, 12)],
            bm25_evidences=_dedupe_evidences(all_bm25)[: max(top_k * 4, 12)],
            query_variants=variants,
            entity_hints=list(plan.entity_hints),
        )


hybrid_retriever = HybridRetriever()
