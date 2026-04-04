from __future__ import annotations

from typing import AsyncIterator

from knowledge_retrieval.evidence_organizer import diversify_evidences, merge_parent_evidences, source_family
from knowledge_retrieval.fusion import evidence_dedupe_key, reciprocal_rank_fusion
from knowledge_retrieval.hybrid_retriever import hybrid_retriever
from knowledge_retrieval.query_rewrite import QueryPlan, build_query_plan
from knowledge_retrieval.reranker import rerank_evidences
from knowledge_retrieval.types import Evidence, OrchestratedRetrievalResult, RetrievalStep


class KnowledgeOrchestrator:
    def __init__(self) -> None:
        self.base_dir = None

    def configure(self, base_dir, _model_builder) -> None:
        self.base_dir = base_dir

    def _source_family(self, source_path: str) -> str:
        return source_family(source_path)

    def _final_evidence_limit(self, question_type: str) -> int:
        if question_type in {"multi_hop", "cross_file_aggregation"}:
            return 4
        return 3

    def _has_correlated_retrieval_evidence(
        self,
        vector_evidences: list[Evidence],
        bm25_evidences: list[Evidence],
    ) -> bool:
        if not vector_evidences or not bm25_evidences:
            return False
        vector_sources = {self._source_family(item.source_path) for item in vector_evidences}
        bm25_sources = {self._source_family(item.source_path) for item in bm25_evidences}
        return bool(vector_sources & bm25_sources)

    def _matched_entities(self, query_plan: QueryPlan, evidences: list[Evidence]) -> int:
        if not query_plan.entity_hints:
            return 0
        joined = "\n\n".join(" ".join([item.source_path, item.locator, item.snippet]) for item in evidences).lower()
        return sum(1 for entity in query_plan.entity_hints if entity.lower() in joined)

    def _dedupe_evidence_pool(self, evidences: list[Evidence]) -> list[Evidence]:
        deduped: dict[str, Evidence] = {}
        for evidence in evidences:
            key = evidence_dedupe_key(evidence)
            current = deduped.get(key)
            if current is None or float(evidence.score or 0.0) > float(current.score or 0.0):
                deduped[key] = evidence
        return sorted(deduped.values(), key=lambda item: float(item.score or 0.0), reverse=True)

    def _entity_targeted_query_plan(self, entity: str, query_plan: QueryPlan) -> QueryPlan:
        keyword_hints = [hint for hint in query_plan.keyword_hints if hint.lower() != entity.lower()]
        query = " ".join([entity] + keyword_hints[:5]).strip()
        variants = [query]
        if any(term in query_plan.original_query for term in ("财报", "报告", "Q3", "三季度", "前三季度")):
            variants.append(" ".join([entity, "财报", "第三季度报告", "Q3", "2025"]).strip())
        return QueryPlan(
            original_query=query,
            question_type=query_plan.question_type if query_plan.question_type in {"compare", "negation", "multi_hop"} else "direct_fact",
            query_variants=[item for item in variants if item],
            entity_hints=[entity],
            keyword_hints=keyword_hints,
        )

    def _family_overview_query_plan(self, query_plan: QueryPlan, entity: str | None = None) -> QueryPlan:
        if entity:
            original = " ".join([entity] + query_plan.keyword_hints[:5]).strip()
            variants = [original]
            canonical = " ".join([entity] + query_plan.keyword_hints[:3] + ["财报", "报告"]).strip()
            if canonical and canonical not in variants:
                variants.append(canonical)
            return QueryPlan(
                original_query=original,
                question_type=query_plan.question_type,
                query_variants=variants,
                entity_hints=[entity],
                keyword_hints=list(query_plan.keyword_hints),
            )
        return QueryPlan(
            original_query=query_plan.original_query,
            question_type=query_plan.question_type,
            query_variants=list(query_plan.query_variants),
            entity_hints=list(query_plan.entity_hints),
            keyword_hints=list(query_plan.keyword_hints),
        )

    def _collect_family_overview_evidences(
        self,
        query_plan: QueryPlan,
        *,
        top_k: int,
    ) -> tuple[list[Evidence], RetrievalStep | None]:
        if query_plan.question_type not in {"fuzzy", "cross_file_aggregation", "compare"}:
            return [], None

        targeted: list[Evidence] = []
        if query_plan.question_type == "cross_file_aggregation" and query_plan.entity_hints:
            for entity in query_plan.entity_hints[:4]:
                entity_plan = self._family_overview_query_plan(query_plan, entity=entity)
                entity_result = hybrid_retriever.retrieve(
                    entity_plan.original_query,
                    top_k=max(top_k, 3),
                    query_plan=entity_plan,
                    chunk_types=["family_overview"],
                )
                overview_candidates = reciprocal_rank_fusion(
                    [entity_result.vector_evidences, entity_result.bm25_evidences],
                    top_k=max(top_k, 3),
                )
                best = next(
                    (
                        item
                        for item in overview_candidates
                        if entity.lower() in f"{item.source_path} {item.snippet}".lower()
                    ),
                    overview_candidates[0] if overview_candidates else None,
                )
                if best is None:
                    continue
                targeted.append(
                    Evidence(
                        source_path=best.source_path,
                        source_type=best.source_type,
                        locator=best.locator,
                        snippet=best.snippet,
                        channel="fused",
                        score=float(best.score or 0.0) + 0.4,
                        parent_id=best.parent_id,
                        query_variant=entity_plan.original_query,
                        supporting_children=best.supporting_children,
                        page=best.page,
                        bbox=best.bbox,
                        element_type=best.element_type,
                        section_title=best.section_title,
                        derived_json_path=best.derived_json_path,
                        derived_markdown_path=best.derived_markdown_path,
                        chunk_type=best.chunk_type,
                    )
                )
        else:
            overview_result = hybrid_retriever.retrieve(
                query_plan.original_query,
                top_k=max(top_k, 4),
                query_plan=self._family_overview_query_plan(query_plan),
                chunk_types=["family_overview"],
            )
            targeted = reciprocal_rank_fusion(
                [overview_result.vector_evidences, overview_result.bm25_evidences],
                top_k=max(top_k, 4),
            )

        if not targeted:
            return [], None

        deduped = self._dedupe_evidence_pool(targeted)
        return deduped, RetrievalStep(
            kind="knowledge",
            stage="family_overview",
            title="Family-level overview recall",
            message="A lightweight PDF-family overview pass selected likely report families before chunk-level retrieval.",
            results=deduped,
        )

    def _collect_entity_targeted_evidences(
        self,
        query_plan: QueryPlan,
        *,
        top_k: int,
        path_filters: list[str] | None = None,
    ) -> tuple[list[Evidence], RetrievalStep | None]:
        if query_plan.question_type != "cross_file_aggregation" or not query_plan.entity_hints:
            return [], None

        targeted: list[Evidence] = []
        for entity in query_plan.entity_hints[:4]:
            entity_plan = self._entity_targeted_query_plan(entity, query_plan)
            entity_result = hybrid_retriever.retrieve(
                entity_plan.original_query,
                top_k=max(top_k, 3),
                query_plan=entity_plan,
                path_filters=path_filters,
            )
            fused = reciprocal_rank_fusion(
                [entity_result.vector_evidences, entity_result.bm25_evidences],
                top_k=max(top_k * 2, 6),
            )
            reranked = rerank_evidences(entity_plan, fused, top_k=max(top_k, 3))
            best = next(
                (
                    evidence
                    for evidence in reranked
                    if entity.lower() in f"{evidence.source_path} {evidence.snippet}".lower()
                ),
                None,
            )
            if best is None:
                continue
            targeted.append(
                Evidence(
                    source_path=best.source_path,
                    source_type=best.source_type,
                    locator=best.locator,
                    snippet=best.snippet,
                    channel=best.channel,
                    score=float(best.score or 0.0) + 0.35,
                    parent_id=best.parent_id,
                    query_variant=entity_plan.original_query,
                    supporting_children=best.supporting_children,
                    page=best.page,
                    bbox=best.bbox,
                    element_type=best.element_type,
                    section_title=best.section_title,
                    derived_json_path=best.derived_json_path,
                    derived_markdown_path=best.derived_markdown_path,
                    chunk_type=best.chunk_type,
                )
            )

        if not targeted:
            return [], None

        return targeted, RetrievalStep(
            kind="knowledge",
            stage="entity_targeted",
            title="Entity-targeted retrieval",
            message="Entity-aware retrieval reserved at least one candidate per target entity before final ranking.",
            results=targeted,
        )

    def _collect_compare_targeted_evidences(
        self,
        query_plan: QueryPlan,
        *,
        top_k: int,
        path_filters: list[str] | None = None,
    ) -> tuple[list[Evidence], RetrievalStep | None]:
        if query_plan.question_type != "compare" or len(query_plan.entity_hints) < 2:
            return [], None

        targeted: list[Evidence] = []
        for entity in query_plan.entity_hints[:4]:
            entity_plan = self._entity_targeted_query_plan(entity, query_plan)
            entity_result = hybrid_retriever.retrieve(
                entity_plan.original_query,
                top_k=max(top_k, 3),
                query_plan=entity_plan,
                path_filters=path_filters,
            )
            fused = reciprocal_rank_fusion(
                [entity_result.vector_evidences, entity_result.bm25_evidences],
                top_k=max(top_k * 2, 6),
            )
            reranked = rerank_evidences(
                entity_plan,
                fused,
                top_k=max(top_k, 3),
                preferred_families=path_filters,
            )
            best = next(
                (
                    evidence
                    for evidence in reranked
                    if entity.lower() in f"{evidence.source_path} {evidence.snippet}".lower()
                ),
                reranked[0] if reranked else None,
            )
            if best is None:
                continue
            targeted.append(
                Evidence(
                    source_path=best.source_path,
                    source_type=best.source_type,
                    locator=best.locator,
                    snippet=best.snippet,
                    channel=best.channel,
                    score=float(best.score or 0.0) + 0.3,
                    parent_id=best.parent_id,
                    query_variant=entity_plan.original_query,
                    supporting_children=best.supporting_children,
                    page=best.page,
                    bbox=best.bbox,
                    element_type=best.element_type,
                    section_title=best.section_title,
                    derived_json_path=best.derived_json_path,
                    derived_markdown_path=best.derived_markdown_path,
                    chunk_type=best.chunk_type,
                )
            )

        if not targeted:
            return [], None

        return targeted, RetrievalStep(
            kind="knowledge",
            stage="compare_targeted",
            title="Compare-targeted retrieval",
            message="Entity-aware retrieval reserved one value-rich candidate per comparison target before final ranking.",
            results=targeted,
        )

    def _collect_focus_targeted_evidences(
        self,
        query_plan: QueryPlan,
        *,
        top_k: int,
        path_filters: list[str] | None = None,
    ) -> tuple[list[Evidence], RetrievalStep | None]:
        if query_plan.question_type not in {"negation", "multi_hop"} or not query_plan.entity_hints:
            return [], None

        targeted: list[Evidence] = []
        for entity in query_plan.entity_hints[:3]:
            entity_plan = self._entity_targeted_query_plan(entity, query_plan)
            entity_result = hybrid_retriever.retrieve(
                entity_plan.original_query,
                top_k=max(top_k, 4),
                query_plan=entity_plan,
                path_filters=path_filters,
            )
            fused = reciprocal_rank_fusion(
                [entity_result.vector_evidences, entity_result.bm25_evidences],
                top_k=max(top_k * 2, 6),
            )
            reranked = rerank_evidences(
                entity_plan,
                fused,
                top_k=max(top_k, 3),
                preferred_families=path_filters,
            )
            best = next(
                (
                    evidence
                    for evidence in reranked
                    if entity.lower() in f"{evidence.source_path} {evidence.snippet}".lower()
                ),
                reranked[0] if reranked else None,
            )
            if best is None:
                continue
            targeted.append(
                Evidence(
                    source_path=best.source_path,
                    source_type=best.source_type,
                    locator=best.locator,
                    snippet=best.snippet,
                    channel=best.channel,
                    score=float(best.score or 0.0) + 0.25,
                    parent_id=best.parent_id,
                    query_variant=entity_plan.original_query,
                    supporting_children=best.supporting_children,
                    page=best.page,
                    bbox=best.bbox,
                    element_type=best.element_type,
                    section_title=best.section_title,
                    derived_json_path=best.derived_json_path,
                    derived_markdown_path=best.derived_markdown_path,
                    chunk_type=best.chunk_type,
                )
            )

        if not targeted:
            return [], None

        return targeted, RetrievalStep(
            kind="knowledge",
            stage="focused_targeted",
            title="Focused targeted retrieval",
            message="Entity-aware targeted retrieval pulled PDF-family candidates for the requested focus question before final ranking.",
            results=targeted,
        )

    def _has_negative_evidence(self, evidences: list[Evidence]) -> bool:
        negative_terms = ("亏损", "未盈利", "净利润为负", "利润为负", "负值", "下降")
        for evidence in evidences:
            snippet = str(evidence.snippet or "")
            if any(term in snippet for term in negative_terms):
                return True
        return False

    def _determine_status(
        self,
        query_plan: QueryPlan,
        *,
        vector_evidences: list[Evidence],
        bm25_evidences: list[Evidence],
        final_evidences: list[Evidence],
    ) -> tuple[str, str]:
        if not final_evidences:
            return (
                "not_found",
                "The current knowledge index does not contain enough evidence for this question. Do not fall back to skill or general-purpose tools to read the source files.",
            )

        family_count = len({self._source_family(item.source_path) for item in final_evidences})
        has_corroboration = self._has_correlated_retrieval_evidence(vector_evidences, bm25_evidences)
        merged_child_support = max((int(item.supporting_children or 1) for item in final_evidences), default=1)
        matched_entities = self._matched_entities(query_plan, final_evidences)

        if query_plan.question_type == "cross_file_aggregation":
            needed_entities = max(2, len(query_plan.entity_hints))
            if family_count >= needed_entities and matched_entities >= needed_entities:
                return (
                    "success",
                    "Returned diversified evidence across multiple indexed sources for cross-file aggregation.",
                )
            return (
                "partial",
                "The knowledge index found only partial cross-file coverage. Answer conservatively and do not complete the aggregation beyond the cited evidence.",
            )

        if query_plan.question_type == "compare":
            if family_count >= 2 and matched_entities >= min(2, len(query_plan.entity_hints) or 2):
                return (
                    "success",
                    "Returned diversified indexed evidence for the requested comparison.",
                )
            return (
                "partial",
                "The knowledge index found only partial comparison coverage. Do not complete the comparison beyond the cited evidence.",
            )

        if query_plan.question_type == "multi_hop":
            if has_corroboration and (family_count >= 2 or merged_child_support >= 3):
                return (
                    "success",
                    "Returned multi-part indexed evidence with corroboration across channels.",
                )
            return (
                "partial",
                "The knowledge index found only partial multi-hop evidence. Answer only with the supported pieces and keep missing links explicit.",
            )

        if query_plan.question_type == "negation":
            if has_corroboration and self._has_negative_evidence(final_evidences):
                return (
                    "success",
                    "Returned indexed evidence that directly supports the requested negative or loss-related conclusion.",
                )
            return (
                "partial",
                "The knowledge index did not return enough direct negative evidence. Do not infer loss or non-profitability beyond the cited evidence.",
            )

        if has_corroboration or merged_child_support >= 2:
            return (
                "success",
                "Returned evidence from the formal indexed retrieval path with corroboration across vector and BM25.",
            )
        return (
            "partial",
            "The knowledge index returned only weak or single-channel evidence. Respond with a partial answer instead of reading source files via skill or tools.",
        )

    def _build_formal_retrieval_result(
        self,
        query: str,
        *,
        top_k: int = 4,
    ) -> OrchestratedRetrievalResult:
        query_plan = build_query_plan(query, prefer_llm=True)
        overview_evidences, overview_step = self._collect_family_overview_evidences(query_plan, top_k=top_k)
        preferred_families = [item.source_path for item in overview_evidences]
        family_path_filters: list[str] | None = None
        if query_plan.question_type in {"fuzzy", "cross_file_aggregation", "compare"} and preferred_families:
            family_path_filters = preferred_families

        hybrid_result = hybrid_retriever.retrieve(
            query,
            top_k=top_k,
            query_plan=query_plan,
            path_filters=family_path_filters,
        )
        if family_path_filters and not hybrid_result.vector_evidences and not hybrid_result.bm25_evidences:
            hybrid_result = hybrid_retriever.retrieve(
                query,
                top_k=top_k,
                query_plan=query_plan,
                path_filters=None,
            )
            family_path_filters = None

        steps: list[RetrievalStep] = [
            RetrievalStep(
                kind="knowledge",
                stage="indexed_retrieval",
                title="Formal knowledge retrieval",
                message="Run only the formal indexed retrieval path (query rewrites + vector + BM25). Do not fall back to skill or general-purpose file-reading tools.",
            )
        ]

        if len(hybrid_result.query_variants) > 1:
            steps.append(
                RetrievalStep(
                    kind="knowledge",
                    stage="query_rewrite",
                    title="Query rewrites",
                    message=" | ".join(hybrid_result.query_variants[:5]),
                )
            )
        if overview_step is not None:
            steps.append(overview_step)

        if hybrid_result.vector_evidences:
            steps.append(
                RetrievalStep(
                    kind="knowledge",
                    stage="vector",
                    title="Vector retrieval",
                    message="Vector retrieval returned indexed evidence candidates from the original query and lightweight rewrites.",
                    results=hybrid_result.vector_evidences[: max(top_k * 2, 8)],
                )
            )
        if hybrid_result.bm25_evidences:
            steps.append(
                RetrievalStep(
                    kind="knowledge",
                    stage="bm25",
                    title="BM25 retrieval",
                    message="BM25 retrieval returned indexed evidence candidates from the original query and lightweight rewrites.",
                    results=hybrid_result.bm25_evidences[: max(top_k * 2, 8)],
                )
            )

        entity_targeted, entity_step = self._collect_entity_targeted_evidences(
            query_plan,
            top_k=top_k,
            path_filters=family_path_filters,
        )
        if entity_step is not None:
            steps.append(entity_step)

        compare_targeted, compare_step = self._collect_compare_targeted_evidences(
            query_plan,
            top_k=top_k,
            path_filters=family_path_filters,
        )
        if compare_step is not None:
            steps.append(compare_step)
        focus_targeted, focus_step = self._collect_focus_targeted_evidences(
            query_plan,
            top_k=top_k,
            path_filters=family_path_filters,
        )
        if focus_step is not None:
            steps.append(focus_step)

        fused = reciprocal_rank_fusion(
            [hybrid_result.vector_evidences, hybrid_result.bm25_evidences],
            top_k=max(top_k * 4, 14),
        )
        if fused:
            steps.append(
                RetrievalStep(
                    kind="knowledge",
                    stage="fused",
                    title="Fused evidence",
                    message="Reciprocal-rank fusion merged candidates from the multi-query indexed retrieval path.",
                    results=fused,
                )
            )

        candidate_pool = self._dedupe_evidence_pool(fused + entity_targeted + compare_targeted + focus_targeted)
        reranked = rerank_evidences(
            query_plan,
            candidate_pool,
            top_k=max(top_k * 3, 12),
            preferred_families=preferred_families,
        )
        if reranked:
            steps.append(
                RetrievalStep(
                    kind="knowledge",
                    stage="rerank",
                    title="Heuristic rerank",
                    message="Lightweight heuristic rerank prioritized entity, time, financial-term, and question-type matches.",
                    results=reranked,
                )
            )

        merged = merge_parent_evidences(reranked, top_k=max(top_k * 3, 12))
        if merged:
            steps.append(
                RetrievalStep(
                    kind="knowledge",
                    stage="parent_merge",
                    title="Parent merge",
                    message="Child chunks from the same parent were merged into richer parent-level evidence before answer generation.",
                    results=merged,
                )
            )

        final_limit = self._final_evidence_limit(query_plan.question_type)
        diversified = diversify_evidences(
            merged,
            question_type=query_plan.question_type,
            entity_hints=query_plan.entity_hints,
            top_k=final_limit,
        )
        final_evidences = diversified or merged or reranked or candidate_pool
        if diversified:
            steps.append(
                RetrievalStep(
                    kind="knowledge",
                    stage="diversified",
                    title="Diversified evidence pick",
                    message="Final evidence selection enforced lightweight source diversification so one document family would not dominate the context.",
                    results=diversified[:final_limit],
                )
            )

        status, reason = self._determine_status(
            query_plan,
            vector_evidences=hybrid_result.vector_evidences + entity_targeted + compare_targeted + focus_targeted,
            bm25_evidences=hybrid_result.bm25_evidences + entity_targeted + compare_targeted + focus_targeted,
            final_evidences=final_evidences,
        )

        return OrchestratedRetrievalResult(
            status=status,
            evidences=final_evidences[:final_limit],
            steps=steps,
            fallback_used=False,
            reason=reason,
            question_type=query_plan.question_type,
            entity_hints=list(query_plan.entity_hints),
        )

    async def astream(self, query: str) -> AsyncIterator[dict]:
        yield {
            "type": "orchestrated_result",
            "result": self._build_formal_retrieval_result(query),
        }


knowledge_orchestrator = KnowledgeOrchestrator()
