from __future__ import annotations

from typing import AsyncIterator

from knowledge_retrieval.fusion import reciprocal_rank_fusion
from knowledge_retrieval.hybrid_retriever import hybrid_retriever
from knowledge_retrieval.types import Evidence, OrchestratedRetrievalResult, RetrievalStep


class KnowledgeOrchestrator:
    def __init__(self) -> None:
        self.base_dir = None

    def configure(self, base_dir, _model_builder) -> None:
        self.base_dir = base_dir

    def _normalize_source_path(self, source_path: str) -> str:
        normalized = source_path.replace("\\", "/").strip()
        if "knowledge/" in normalized and not normalized.startswith("knowledge/"):
            normalized = normalized[normalized.index("knowledge/") :]
        return normalized

    def _has_correlated_retrieval_evidence(
        self,
        vector_evidences: list[Evidence],
        bm25_evidences: list[Evidence],
    ) -> bool:
        if not vector_evidences or not bm25_evidences:
            return False
        vector_sources = {self._normalize_source_path(item.source_path) for item in vector_evidences}
        bm25_sources = {self._normalize_source_path(item.source_path) for item in bm25_evidences}
        return bool(vector_sources & bm25_sources)

    def _build_formal_retrieval_result(
        self,
        query: str,
        *,
        top_k: int = 4,
    ) -> OrchestratedRetrievalResult:
        hybrid_result = hybrid_retriever.retrieve(query, top_k=top_k)
        steps: list[RetrievalStep] = [
            RetrievalStep(
                kind="knowledge",
                stage="indexed_retrieval",
                title="Formal knowledge retrieval",
                message="Run only the formal indexed retrieval path (vector + BM25). Do not fall back to skill or general-purpose file-reading tools.",
            )
        ]

        if hybrid_result.vector_evidences:
            steps.append(
                RetrievalStep(
                    kind="knowledge",
                    stage="vector",
                    title="Vector retrieval",
                    message="Vector retrieval returned indexed evidence candidates.",
                    results=hybrid_result.vector_evidences,
                )
            )
        if hybrid_result.bm25_evidences:
            steps.append(
                RetrievalStep(
                    kind="knowledge",
                    stage="bm25",
                    title="BM25 retrieval",
                    message="BM25 retrieval returned indexed evidence candidates.",
                    results=hybrid_result.bm25_evidences,
                )
            )

        fused = reciprocal_rank_fusion(
            [
                hybrid_result.vector_evidences,
                hybrid_result.bm25_evidences,
            ],
            top_k=6,
        )
        has_correlated_evidence = self._has_correlated_retrieval_evidence(
            hybrid_result.vector_evidences,
            hybrid_result.bm25_evidences,
        )

        if fused:
            steps.append(
                RetrievalStep(
                    kind="knowledge",
                    stage="fused",
                    title="Fused evidence",
                    message="Use fused evidence from indexed retrieval only.",
                    results=fused,
                )
            )
            if has_correlated_evidence:
                return OrchestratedRetrievalResult(
                    status="success",
                    evidences=fused,
                    steps=steps,
                    fallback_used=False,
                    reason="Returned evidence from the formal indexed retrieval path with corroboration across vector and BM25.",
                )
            return OrchestratedRetrievalResult(
                status="partial",
                evidences=fused,
                steps=steps,
                fallback_used=False,
                reason="The knowledge index returned only weak or single-channel evidence. Respond with a partial answer instead of reading source files via skill or tools.",
            )

        partial_evidences: list[Evidence] = list(hybrid_result.vector_evidences) + list(hybrid_result.bm25_evidences)
        if partial_evidences:
            return OrchestratedRetrievalResult(
                status="partial",
                evidences=partial_evidences[:6],
                steps=steps,
                fallback_used=False,
                reason="The knowledge index returned only partial evidence. Do not fall back to skill or general-purpose tools to read the source files.",
            )

        return OrchestratedRetrievalResult(
            status="not_found",
            evidences=[],
            steps=steps,
            fallback_used=False,
            reason="The current knowledge index does not contain enough evidence for this question. Do not fall back to skill or general-purpose tools to read the source files.",
        )

    async def astream(self, query: str) -> AsyncIterator[dict]:
        yield {
            "type": "orchestrated_result",
            "result": self._build_formal_retrieval_result(query),
        }


knowledge_orchestrator = KnowledgeOrchestrator()
