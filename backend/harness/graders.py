"""Explicit graders and benchmark judges used by the harness runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from harness.types import GuardResult


class KnowledgeGuardSupport(Protocol):
    """Agent helper surface needed by the knowledge-answer grader."""

    def _knowledge_support_corpus(self, retrieval_result) -> str: ...

    def _unsupported_knowledge_details(self, answer: str, support_corpus: str) -> dict[str, list[str]]: ...

    def _all_sources_are_directory_guides(self, retrieval_result) -> bool: ...

    def _build_conservative_knowledge_answer(
        self,
        retrieval_result,
        *,
        unsupported_numbers: list[str] | None = None,
        unsupported_locators: list[str] | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class KnowledgeGuardDecision:
    """Result of grading one knowledge answer against retrieved evidence."""

    final_answer: str
    guard_result: GuardResult | None

    @property
    def downgraded(self) -> bool:
        return self.guard_result is not None


class KnowledgeAnswerGrader:
    """Surface the current knowledge-answer guard as an explicit harness grader."""

    def __init__(self, support: KnowledgeGuardSupport) -> None:
        self._support = support

    def grade(self, answer: str, retrieval_result) -> KnowledgeGuardDecision:
        if retrieval_result is None:
            return KnowledgeGuardDecision(final_answer=str(answer or ""), guard_result=None)

        normalized_answer = str(answer or "").strip()
        status = str(getattr(retrieval_result, "status", "") or "").strip().lower()
        question_type = str(getattr(retrieval_result, "question_type", "") or "").strip().lower()
        support_corpus = self._support._knowledge_support_corpus(retrieval_result)

        if not normalized_answer:
            return self._downgrade(
                retrieval_result,
                trigger="empty_answer",
                reason="knowledge answer was empty",
                question_type=question_type,
                status=status,
                original_answer=str(answer or ""),
            )

        unsupported = self._support._unsupported_knowledge_details(normalized_answer, support_corpus)
        unsupported_numbers = unsupported.get("numbers", [])
        unsupported_locators = unsupported.get("locators", [])
        if unsupported_numbers or unsupported_locators:
            return self._downgrade(
                retrieval_result,
                trigger="unsupported_numbers_or_locators",
                reason="knowledge answer contained unsupported numeric or locator details",
                question_type=question_type,
                status=status,
                original_answer=normalized_answer,
                unsupported_numbers=unsupported_numbers,
                unsupported_locators=unsupported_locators,
            )

        if status in {"partial", "not_found"} and self._support._all_sources_are_directory_guides(retrieval_result):
            return self._downgrade(
                retrieval_result,
                trigger="directory_guides_only",
                reason="knowledge answer relied on directory-guide sources only",
                question_type=question_type,
                status=status,
                original_answer=normalized_answer,
            )

        return KnowledgeGuardDecision(final_answer=normalized_answer, guard_result=None)

    def _downgrade(
        self,
        retrieval_result,
        *,
        trigger: str,
        reason: str,
        question_type: str,
        status: str,
        original_answer: str,
        unsupported_numbers: list[str] | None = None,
        unsupported_locators: list[str] | None = None,
        unsupported_inference_terms: list[str] | None = None,
    ) -> KnowledgeGuardDecision:
        conservative_answer = self._support._build_conservative_knowledge_answer(
            retrieval_result,
            unsupported_numbers=unsupported_numbers,
            unsupported_locators=unsupported_locators,
        )
        guard_result = GuardResult(
            name="knowledge_grounding_guard",
            passed=False,
            reason=reason,
            details={
                "trigger": trigger,
                "question_type": question_type,
                "status": status,
                "unsupported_numbers": list(unsupported_numbers or []),
                "unsupported_locators": list(unsupported_locators or []),
                "unsupported_inference_terms": list(unsupported_inference_terms or []),
                "original_answer": original_answer,
                "corrected_answer": conservative_answer,
            },
        )
        return KnowledgeGuardDecision(final_answer=conservative_answer, guard_result=guard_result)


@dataclass(frozen=True)
class BenchmarkJudgeResult:
    passed: bool
    score: float
    reason: str = ""
    dimensions: dict[str, bool] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "reason": self.reason,
            "dimensions": dict(self.dimensions),
            "details": dict(self.details),
        }


class HarnessBenchmarkJudge:
    """Judge benchmark outcomes with a mix of deterministic checks and case expectations."""

    def judge_case(self, case: Any, result: Mapping[str, Any]) -> BenchmarkJudgeResult:
        expect = dict(getattr(case, "expect", {}) or {})
        judge_expect = dict(expect.get("judge", {}) or {})
        final_answer = str((result.get("outcome") or {}).get("final_answer", "") or "")
        dimensions: dict[str, bool] = {}

        if "route_intent" in expect:
            dimensions["route_reasonable"] = bool(result.get("route_correct"))
        if "retrieval" in expect:
            expected_retrieval = bool(expect.get("retrieval"))
            actual_retrieval = bool(result.get("retrieval_trace_present"))
            dimensions["retrieval_necessary"] = actual_retrieval == expected_retrieval
        if "tool" in expect:
            expected_tool = bool(expect.get("tool"))
            actual_tool = bool(result.get("tool_trace_present"))
            dimensions["tool_necessary"] = actual_tool == expected_tool
        if "guard" in expect:
            dimensions["guard_behavior"] = bool(result.get("guard_correct"))

        must_contain = [str(item) for item in judge_expect.get("must_contain", []) or [] if str(item).strip()]
        must_not_contain = [str(item) for item in judge_expect.get("must_not_contain", []) or [] if str(item).strip()]
        has_answer_surface = result.get("final_answer_present") is not None or bool(final_answer.strip())
        if has_answer_surface:
            dimensions["answer_presence"] = bool(final_answer.strip()) if not expect.get("failure") else True
            dimensions["contains_required_clues"] = all(token in final_answer for token in must_contain) if must_contain else True
            dimensions["avoids_forbidden_clues"] = all(token not in final_answer for token in must_not_contain) if must_not_contain else True

        expect_partial = judge_expect.get("expect_partial")
        if expect_partial is not None and has_answer_surface:
            is_partial_answer = ("当前证据未显示" in final_answer) or bool(result.get("guard_present"))
            dimensions["partiality"] = is_partial_answer == bool(expect_partial)

        unsupported_terms = [str(item) for item in judge_expect.get("unsupported_terms", []) or [] if str(item).strip()]
        if unsupported_terms and has_answer_surface:
            dimensions["unsupported_claim_control"] = all(term not in final_answer for term in unsupported_terms)

        reflection_terms = [str(item) for item in judge_expect.get("reflection_terms", []) or [] if str(item).strip()]
        if reflection_terms and has_answer_surface:
            dimensions["tool_or_evidence_reflection"] = all(term in final_answer for term in reflection_terms)

        passed = all(dimensions.values()) if dimensions else True
        score = round(sum(1 for value in dimensions.values() if value) / len(dimensions), 4) if dimensions else 1.0
        failed_dims = [name for name, ok in dimensions.items() if not ok]
        return BenchmarkJudgeResult(
            passed=passed,
            score=score,
            reason=";".join(failed_dims),
            dimensions=dimensions,
            details={
                "must_contain": must_contain,
                "must_not_contain": must_not_contain,
                "unsupported_terms": unsupported_terms,
            },
        )
