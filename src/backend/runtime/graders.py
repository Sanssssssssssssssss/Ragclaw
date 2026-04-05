"""Explicit graders and benchmark judges used by the harness runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from benchmarks.judge_client import JudgeClient, load_judge_client

from src.backend.observability.types import GuardResult


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
        rewritten_query = str((result.get("outcome") or {}).get("rewritten_query", "") or "")

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

        must_preserve_terms = [str(item) for item in expect.get("must_preserve_terms", []) or [] if str(item).strip()]
        if must_preserve_terms and rewritten_query:
            dimensions["rewrite_preserves_intent"] = all(term in rewritten_query for term in must_preserve_terms)

        must_not_introduce_terms = [
            str(item) for item in expect.get("must_not_introduce_terms", []) or [] if str(item).strip()
        ]
        if must_not_introduce_terms and rewritten_query:
            dimensions["rewrite_avoids_invention"] = all(term not in rewritten_query for term in must_not_introduce_terms)

        expected_question_type = str(expect.get("question_type", "") or "").strip()
        actual_question_type = str((result.get("outcome") or {}).get("question_type", "") or "").strip()
        if expected_question_type:
            dimensions["planner_reasonable"] = actual_question_type == expected_question_type

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


@dataclass(frozen=True)
class HarnessLLMJudgeResult:
    passed: bool | None
    score: float | None
    reason: str = ""
    dimensions: dict[str, bool] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "reason": self.reason,
            "dimensions": dict(self.dimensions),
            "details": dict(self.details),
            "error": self.error,
        }


class HarnessLLMJudge:
    """Model-based benchmark judge for soft routing, grounding, and rewrite quality."""

    def __init__(self, client: JudgeClient | None = None) -> None:
        self._client = client

    @staticmethod
    def _normalize_dimensions(dimensions: Mapping[str, Any]) -> dict[str, bool]:
        aliases = {
            "route_reasonable": "route_reasonable",
            "route_correct": "route_reasonable",
            "retrieval_necessary": "retrieval_necessary",
            "retrieval_reasonable": "retrieval_necessary",
            "retrieval_appropriate": "retrieval_necessary",
            "tool_necessary": "tool_necessary",
            "tool_use_reasonable": "tool_necessary",
            "tool_usage_reasonable": "tool_necessary",
            "tool_selection_reasonable": "tool_necessary",
            "rewrite_preserves_intent": "rewrite_preserves_intent",
            "rewrite_reasonable": "rewrite_preserves_intent",
            "planner_reasonable": "planner_reasonable",
            "rewrite_planner_reasonable": "planner_reasonable",
            "grounded_answer": "grounded_answer",
            "answer_grounded": "grounded_answer",
            "final_answer_reasonable": "grounded_answer",
            "partiality_honest": "partiality_honest",
            "honesty": "partiality_honest",
            "final_answer_honest": "partiality_honest",
            "conflicting_evidence_honesty": "conflicting_evidence_honesty",
            "tool_or_evidence_reflection": "tool_or_evidence_reflection",
            "unsupported_claim_control": "unsupported_claim_control",
        }
        normalized: dict[str, bool] = {}
        for raw_key, value in dimensions.items():
            canonical = aliases.get(str(raw_key), str(raw_key))
            normalized[canonical] = bool(value)
        return normalized

    @classmethod
    def from_env(cls) -> "HarnessLLMJudge":
        return cls(load_judge_client())

    @property
    def available(self) -> bool:
        return self._client is not None

    def judge_case(
        self,
        case: Any,
        result: Mapping[str, Any],
        *,
        deterministic_judge: Mapping[str, Any] | None = None,
    ) -> HarnessLLMJudgeResult:
        if self._client is None:
            return HarnessLLMJudgeResult(
                passed=None,
                score=None,
                error="judge_unavailable",
            )

        payload = {
            "case_id": getattr(case, "case_id", ""),
            "suite": getattr(case, "suite", ""),
            "runner": getattr(case, "runner", ""),
            "bucket": getattr(case, "bucket", ""),
            "scenario": getattr(case, "scenario", ""),
            "message": getattr(case, "message", ""),
            "answer": getattr(case, "answer", ""),
            "expect": dict(getattr(case, "expect", {}) or {}),
            "retrieval_result": dict(getattr(case, "retrieval_result", {}) or {}) or None,
            "benchmark_result": dict(result),
            "deterministic_judge": dict(deterministic_judge or {}),
        }
        try:
            judged = self._client.judge_harness_case(payload)
            return HarnessLLMJudgeResult(
                passed=bool(judged.get("passed", False)),
                score=float(judged.get("score", 0.0) or 0.0),
                reason=str(judged.get("reason", "") or "").strip(),
                dimensions=self._normalize_dimensions(dict(judged.get("dimensions", {}) or {})),
                details=dict(judged.get("details", {}) or {}),
            )
        except Exception as exc:  # pragma: no cover - network/model failures are environment-specific
            return HarnessLLMJudgeResult(
                passed=None,
                score=None,
                error=str(exc) or exc.__class__.__name__,
            )
