from __future__ import annotations

from collections import defaultdict
import re
import unicodedata
from typing import Any

try:
    from .case_loader import QUESTION_TYPES, normalize_source_path
except ImportError:  # pragma: no cover - fallback for running inside backend cwd
    from benchmarks.case_loader import QUESTION_TYPES, normalize_source_path


BENCHMARK_CATEGORIES = (
    "routing",
    "retrieval",
    "tool_use",
    "constraint_following",
    "groundedness",
)
RAG_SUBTYPES = ("retrieval", "grounding", "ranking", "table")


def _normalized_text(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _canonical_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower().strip()
    normalized = normalized.replace("\\", "/")
    normalized = re.sub(r"[`'\"“”‘’]+", "", normalized)
    normalized = re.sub(r"[\(\)\[\]\{\}:：;,，。！？、\-_/]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _compact_text(value: str) -> str:
    canonical = _canonical_text(value)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", canonical)


def _contains_term(haystack: str, needle: str) -> bool:
    raw_haystack = _normalized_text(haystack)
    raw_needle = _normalized_text(needle)
    if raw_needle and raw_needle in raw_haystack:
        return True

    canonical_haystack = _canonical_text(haystack)
    canonical_needle = _canonical_text(needle)
    if canonical_needle and canonical_needle in canonical_haystack:
        return True

    compact_haystack = _compact_text(haystack)
    compact_needle = _compact_text(needle)
    if compact_needle and compact_needle in compact_haystack:
        return True

    return False


def _detect_infra_error(final_answer: str, error_message: str) -> str | None:
    combined = _normalized_text(f"{error_message}\n{final_answer}")
    if not combined:
        return None

    patterns = (
        "rate limit",
        "rate_limit",
        "max rpm",
        "429",
        "missing api key",
        "request timed out",
        "timed out",
        "server disconnected",
        "connection error",
        "connection refused",
    )
    if any(pattern in combined for pattern in patterns):
        return error_message.strip() or final_answer.strip()
    return None


def _rate(values: list[bool]) -> float:
    if not values:
        return 0.0
    return sum(1 for item in values if item) / len(values)


def _rate_or_none(values: list[bool]) -> float | None:
    if not values:
        return None
    return _rate(values)


def _avg_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _normalize_paths(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = normalize_source_path(str(value))
        if candidate and candidate not in seen:
            seen.add(candidate)
            normalized.append(candidate)
    return normalized


def _source_family(path: str) -> str:
    normalized = normalize_source_path(str(path))
    if not normalized:
        return normalized
    parent, _, filename = normalized.rpartition("/")
    lowered = filename.lower()

    if lowered.endswith("_extracted.txt"):
        stem = filename[: -len("_extracted.txt")]
        extension = ".pdf"
    else:
        stem, dot, ext = filename.rpartition(".")
        if not dot:
            stem = filename
            extension = ""
        else:
            extension = f".{ext}"

    if extension.lower() == ".txt" and re.search(r"20\d{2}[_\s-]*q[1-4]|20\d{2}.+(季度|q[1-4])", stem, re.IGNORECASE):
        stem = re.sub(r"[_\s-]*(提取文本|extracted)$", "", stem, flags=re.IGNORECASE)
        extension = ".pdf"

    if extension.lower() == ".pdf":
        stem = re.sub(r"[_\s]+", " ", stem).strip()
    family = f"{stem}{extension}"
    return f"{parent}/{family}" if parent else family


def _normalize_source_families(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = _source_family(str(value))
        if candidate and candidate not in seen:
            seen.add(candidate)
            normalized.append(candidate)
    return normalized


def _effective_expected_route(case: dict[str, Any]) -> str | None:
    explicit = str(case.get("expected_route", "") or "").strip().lower()
    if explicit:
        return explicit
    if str(case.get("module", "")).strip().lower() == "rag":
        return "knowledge"
    return None


def _source_coverage(question_type: str, top_k_sources: list[str], gold_sources: list[str]) -> tuple[float | None, bool]:
    if not gold_sources:
        return None, True

    normalized_top_k = set(_normalize_source_families(top_k_sources))
    normalized_gold = _normalize_source_families(gold_sources)
    if not normalized_gold:
        return None, True

    matched_count = sum(1 for source in normalized_gold if source in normalized_top_k)
    coverage = matched_count / len(normalized_gold)

    if str(question_type or "").strip().lower() == "cross_file_aggregation":
        minimum_hits = min(2, len(normalized_gold))
        retrieval_pass = matched_count >= minimum_hits and coverage >= 0.5
    else:
        retrieval_pass = matched_count >= 1
    return coverage, retrieval_pass


def _evaluate_rag_checks(
    case: dict[str, Any],
    *,
    detected_route: str,
    top_k_sources: list[str],
    retrieval_hit: bool,
    source_coverage: float | None,
    final_answer_non_empty: bool,
    grounded_pass: bool,
    supported_fact_ratio: float,
) -> dict[str, Any]:
    if str(case.get("module", "")).strip().lower() != "rag":
        return {}

    subtype = str(case.get("subtype", "")).strip().lower()
    route_is_knowledge = detected_route == "knowledge"
    if subtype == "retrieval":
        return {
            "subtype": subtype,
            "route_is_knowledge": route_is_knowledge,
            "source_hit_at_k": retrieval_hit,
            "source_coverage": source_coverage,
            "final_answer_non_empty": final_answer_non_empty,
            "top_k_sources": top_k_sources,
        }
    if subtype == "grounding":
        return {
            "subtype": subtype,
            "route_is_knowledge": route_is_knowledge,
            "final_answer_non_empty": final_answer_non_empty,
            "grounded_pass": grounded_pass,
            "required_fact_coverage": supported_fact_ratio,
        }
    if subtype == "ranking":
        return {
            "subtype": subtype,
            "ranking_ready": bool(case.get("gold_chunks")),
            "gold_chunks": list(case.get("gold_chunks", [])),
            "ranking_score": None,
        }
    if subtype == "table":
        return {
            "subtype": subtype,
            "table_ready": bool(case.get("gold_tables") or case.get("gold_fields")),
            "gold_tables": list(case.get("gold_tables", [])),
            "gold_fields": list(case.get("gold_fields", [])),
            "table_pass": None,
        }
    return {"subtype": subtype or None}


def _aggregate_boolean_rate(items: list[dict[str, Any]], extractor) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[bool]] = defaultdict(list)
    for item in items:
        key = extractor(item)
        if key is None:
            continue
        grouped[str(key)].append(bool(item.get("overall_pass")))
    return {
        key: {
            "count": len(values),
            "pass_rate": (sum(1 for value in values if value) / len(values)) if values else 0.0,
        }
        for key, values in grouped.items()
    }


def _question_type_summary(case_results: list[dict[str, Any]]) -> dict[str, dict[str, float | int | None]]:
    grouped: dict[str, list[dict[str, Any]]] = {question_type: [] for question_type in QUESTION_TYPES}
    grouped["other"] = []

    for item in case_results:
        question_type = str(item.get("question_type", "") or "").strip().lower()
        if question_type in grouped:
            grouped[question_type].append(item)
        else:
            grouped["other"].append(item)

    summary: dict[str, dict[str, float | int | None]] = {}
    for question_type, items in grouped.items():
        executed = [item for item in items if not item.get("skipped")]
        judge_cases = [item for item in executed if item.get("judge", {}).get("requested")]
        judge_executed_cases = [item for item in judge_cases if not item.get("judge", {}).get("skipped")]
        route_cases = [item for item in executed if item.get("expected_route")]
        retrieval_cases = [item for item in executed if item.get("gold_sources")]
        final_answer_cases = [item for item in executed if item.get("should_have_final_answer")]
        grounded_cases = [
            item
            for item in executed
            if item.get("must_include") or item.get("must_not_include") or "groundedness" in item.get("categories", [])
        ]
        source_coverages = [
            float(item.get("source_coverage"))
            for item in retrieval_cases
            if item.get("source_coverage") is not None
        ]
        summary[question_type] = {
            "total_cases": len(items),
            "executed_cases": len(executed),
            "route_accuracy": _rate_or_none([bool(item.get("checks", {}).get("route_pass")) for item in route_cases]),
            "retrieval_source_hit_rate": _rate_or_none(
                [bool(item.get("checks", {}).get("retrieval_pass")) for item in retrieval_cases]
            ),
            "source_coverage": _avg_or_none(source_coverages),
            "final_answer_non_empty_rate": _rate_or_none(
                [bool(item.get("final_answer_non_empty")) for item in final_answer_cases]
            ),
            "groundedness_pass_rate": _rate_or_none(
                [bool(item.get("checks", {}).get("grounded_pass")) for item in grounded_cases]
            ),
            "judge_grounded_pass_rate": _rate_or_none(
                [bool(item.get("judge", {}).get("pass")) for item in judge_executed_cases]
            ),
            "judge_correctness_avg": _avg_or_none(
                [float(item.get("judge", {}).get("correctness_score", 0.0) or 0.0) for item in judge_executed_cases]
            ),
        }
    return summary


def _aggregate_modalities(items: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[bool]] = defaultdict(list)
    for item in items:
        modalities = item.get("modalities") or []
        if isinstance(modalities, str):
            modalities = [modalities]
        normalized = [str(modality).strip().lower() for modality in modalities if str(modality).strip()]
        if not normalized:
            grouped["none"].append(bool(item.get("overall_pass")))
            continue
        for modality in normalized:
            grouped[modality].append(bool(item.get("overall_pass")))
    return {
        key: {
            "count": len(values),
            "pass_rate": (sum(1 for value in values if value) / len(values)) if values else 0.0,
        }
        for key, values in grouped.items()
    }


def evaluate_case(case: dict[str, Any], trace: dict[str, Any], indexed_types: set[str]) -> dict[str, Any]:
    required_source_types = {
        str(item).strip().lower()
        for item in case.get("required_source_types", [])
        if str(item).strip()
    }
    if required_source_types and not required_source_types.issubset(indexed_types):
        return {
            "id": case["id"],
            "module": case.get("module"),
            "subtype": case.get("subtype"),
            "question_type": case.get("question_type"),
            "difficulty": case.get("difficulty"),
            "modalities": case.get("modalities", []),
            "category": case.get("category", "unknown"),
            "categories": list(case.get("categories", [case.get("category", "unknown")])),
            "skipped": True,
            "skip_kind": "unsupported_source_type",
            "skip_reason": (
                "Indexed source types missing: " + ", ".join(sorted(required_source_types - indexed_types))
            ),
        }

    detected_route = str(trace.get("detected_route", "unknown"))
    called_tools = list(trace.get("called_tools", []))
    retrieval_sources = list(trace.get("retrieval_sources", []))
    final_answer = str(trace.get("final_answer", "") or "")
    error_message = str(trace.get("error_message", "") or "")
    infra_error = _detect_infra_error(final_answer, error_message)

    if infra_error:
        return {
            "id": case["id"],
            "module": case.get("module"),
            "subtype": case.get("subtype"),
            "question_type": case.get("question_type"),
            "difficulty": case.get("difficulty"),
            "modalities": case.get("modalities", []),
            "category": case.get("category", "unknown"),
            "categories": list(case.get("categories", [case.get("category", "unknown")])),
            "skipped": True,
            "skip_kind": "infrastructure",
            "skip_reason": "Infrastructure error: " + infra_error,
            "input": case.get("input", ""),
            "final_answer": final_answer,
            "error_message": error_message,
        }

    route_expected = _effective_expected_route(case)
    route_pass = detected_route == route_expected if route_expected else True

    expected_tools = [str(item) for item in case.get("expected_tools", [])]
    blocked_tools = [str(item) for item in case.get("blocked_tools", [])]
    tool_set = set(called_tools)
    expected_tool_set = set(expected_tools)
    blocked_tool_set = set(blocked_tools)

    tool_pass = True
    if expected_tools:
        tool_pass = expected_tool_set.issubset(tool_set)
    elif "expected_tools" in case:
        tool_pass = not tool_set

    blocked_tool_violations = sorted(tool_set.intersection(blocked_tool_set))
    if blocked_tool_violations:
        tool_pass = False

    allow_knowledge = case.get("allow_knowledge")
    knowledge_violation = bool(allow_knowledge is False and trace.get("knowledge_used"))
    constraints_pass = not blocked_tool_violations and not knowledge_violation
    if allow_knowledge is False and detected_route == "knowledge":
        constraints_pass = False

    top_k = int(case.get("retrieval_top_k", 5) or 5)
    top_k_sources = retrieval_sources[:top_k]
    gold_sources = [str(item) for item in case.get("gold_sources", []) if str(item).strip()]
    normalized_retrieval_sources = _normalize_paths(retrieval_sources)
    normalized_top_k_sources = _normalize_paths(top_k_sources)
    normalized_gold_sources = _normalize_paths(gold_sources)
    normalized_retrieval_families = _normalize_source_families(retrieval_sources)
    normalized_top_k_families = _normalize_source_families(top_k_sources)
    normalized_gold_families = _normalize_source_families(gold_sources)
    source_coverage, retrieval_hit = _source_coverage(
        str(case.get("question_type", "") or ""),
        top_k_sources,
        gold_sources,
    )

    final_answer_required = bool(case.get("should_have_final_answer", False))
    final_answer_non_empty = bool(final_answer.strip())
    final_answer_pass = (not final_answer_required) or final_answer_non_empty

    must_include = [str(item) for item in case.get("must_include", []) if str(item).strip()]
    must_not_include = [str(item) for item in case.get("must_not_include", []) if str(item).strip()]
    matched_required = [term for term in must_include if _contains_term(final_answer, term)]
    unsupported_hits = [term for term in must_not_include if _contains_term(final_answer, term)]
    supported_fact_ratio = len(matched_required) / max(1, len(must_include))
    grounded_pass = final_answer_pass
    if must_include:
        required_threshold = 1.0 if len(must_include) <= 2 else 0.6
        grounded_pass = grounded_pass and supported_fact_ratio >= required_threshold
    if unsupported_hits:
        grounded_pass = False

    checks = {
        "route_pass": route_pass,
        "tool_pass": tool_pass,
        "constraints_pass": constraints_pass,
        "retrieval_pass": retrieval_hit,
        "final_answer_pass": final_answer_pass,
        "grounded_pass": grounded_pass,
    }
    rag_checks = _evaluate_rag_checks(
        case,
        detected_route=detected_route,
        top_k_sources=normalized_top_k_families,
        retrieval_hit=retrieval_hit,
        source_coverage=source_coverage,
        final_answer_non_empty=final_answer_non_empty,
        grounded_pass=grounded_pass,
        supported_fact_ratio=supported_fact_ratio,
    )

    overall_pass = all(
        value
        for key, value in checks.items()
        if (
            (key == "retrieval_pass" and normalized_gold_sources)
            or (key == "route_pass" and route_expected)
            or (key == "tool_pass" and ("expected_tools" in case or blocked_tools))
            or (key == "constraints_pass" and (blocked_tools or allow_knowledge is False))
            or (key == "final_answer_pass" and final_answer_required)
            or (key == "grounded_pass" and (must_include or must_not_include))
        )
    )

    return {
        "id": case["id"],
        "module": case.get("module"),
        "subtype": case.get("subtype"),
        "question_type": case.get("question_type"),
        "difficulty": case.get("difficulty"),
        "modalities": list(case.get("modalities", [])) if isinstance(case.get("modalities", []), list) else [case.get("modalities")] if case.get("modalities") else [],
        "category": case.get("category", "unknown"),
        "categories": list(case.get("categories", [case.get("category", "unknown")])),
        "skipped": False,
        "input": case.get("input", ""),
        "allow_knowledge": allow_knowledge,
        "detected_route": detected_route,
        "expected_route": route_expected or None,
        "called_tools": called_tools,
        "expected_tools": expected_tools,
        "blocked_tools": blocked_tools,
        "knowledge_used": bool(trace.get("knowledge_used")),
        "retrieval_sources": normalized_retrieval_sources,
        "retrieval_source_families": normalized_retrieval_families,
        "top_k_sources": normalized_top_k_sources,
        "top_k_source_families": normalized_top_k_families,
        "gold_sources": normalized_gold_sources,
        "gold_source_families": normalized_gold_families,
        "source_coverage": source_coverage,
        "gold_chunks": list(case.get("gold_chunks", [])),
        "gold_tables": list(case.get("gold_tables", [])),
        "gold_fields": list(case.get("gold_fields", [])),
        "final_answer": final_answer,
        "error_message": error_message,
        "final_answer_non_empty": final_answer_non_empty,
        "should_have_final_answer": final_answer_required,
        "must_include": must_include,
        "must_not_include": must_not_include,
        "judge_enabled": bool(case.get("judge_enabled", False)),
        "judge_expectations": case.get("judge_expectations") or {},
        "matched_required": matched_required,
        "missing_required": [term for term in must_include if term not in matched_required],
        "unsupported_hits": unsupported_hits,
        "supported_fact_ratio": supported_fact_ratio,
        "violations": {
            "blocked_tools": blocked_tool_violations,
            "knowledge": knowledge_violation,
        },
        "checks": checks,
        "rag_checks": rag_checks,
        "overall_pass": overall_pass,
    }


def summarize_results(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    executed = [item for item in case_results if not item.get("skipped")]
    skipped = [item for item in case_results if item.get("skipped")]
    rag_only = bool(case_results) and all(str(item.get("module", "")).strip().lower() == "rag" for item in case_results)

    def rate(values: list[bool]) -> float:
        return _rate(values)

    route_cases = [item for item in executed if item.get("expected_route")]
    retrieval_cases = [item for item in executed if item.get("gold_sources")]
    tool_cases = [
        item
        for item in executed
        if item.get("expected_tools") is not None and (item.get("expected_tools") or item.get("blocked_tools"))
    ]
    constraint_cases = [
        item
        for item in executed
        if item.get("blocked_tools") or item.get("allow_knowledge") is False
    ]
    final_answer_cases = [item for item in executed if item.get("should_have_final_answer")]
    grounded_cases = [
        item
        for item in executed
        if item.get("must_include") or item.get("must_not_include") or "groundedness" in item.get("categories", [])
    ]
    rag_cases = [item for item in executed if str(item.get("module", "")).strip().lower() == "rag"]
    rag_retrieval_cases = [item for item in rag_cases if str(item.get("subtype", "")).strip().lower() == "retrieval"]
    rag_grounding_cases = [item for item in rag_cases if str(item.get("subtype", "")).strip().lower() == "grounding"]
    rag_ranking_cases = [item for item in rag_cases if str(item.get("subtype", "")).strip().lower() == "ranking"]
    rag_table_cases = [item for item in rag_cases if str(item.get("subtype", "")).strip().lower() == "table"]
    judge_cases = [item for item in executed if item.get("judge", {}).get("requested")]
    judge_executed_cases = [item for item in judge_cases if not item.get("judge", {}).get("skipped")]

    category_passes: dict[str, list[bool]] = defaultdict(list)
    for item in executed:
        checks = item.get("checks", {})
        for category in item.get("categories", [item.get("category", "unknown")]):
            if category == "routing":
                category_passes[category].append(bool(checks.get("route_pass")))
            elif category == "retrieval":
                category_passes[category].append(bool(checks.get("retrieval_pass")) and bool(checks.get("final_answer_pass")))
            elif category == "tool_use":
                category_passes[category].append(bool(checks.get("tool_pass")) and bool(checks.get("final_answer_pass")))
            elif category == "constraint_following":
                category_passes[category].append(bool(checks.get("constraints_pass")))
            elif category == "groundedness":
                category_passes[category].append(bool(checks.get("grounded_pass")))

    forbidden_violation_cases = [
        item
        for item in constraint_cases
        if item.get("violations", {}).get("blocked_tools") or item.get("violations", {}).get("knowledge")
    ]
    unsupported_cases = [item for item in grounded_cases if item.get("unsupported_hits")]
    judge_unsupported_cases = [item for item in judge_executed_cases if item.get("judge", {}).get("unsupported")]
    required_total = sum(len(item.get("matched_required", [])) + len(item.get("missing_required", [])) for item in grounded_cases)
    required_matched = sum(len(item.get("matched_required", [])) for item in grounded_cases)
    source_coverages = [
        float(item.get("source_coverage"))
        for item in retrieval_cases
        if item.get("source_coverage") is not None
    ]

    return {
        "total_cases": len(case_results),
        "executed_cases": len(executed),
        "skipped_cases": len(skipped),
        "unsupported_skipped_cases": sum(1 for item in skipped if item.get("skip_kind") == "unsupported_source_type"),
        "infrastructure_skipped_cases": sum(1 for item in skipped if item.get("skip_kind") == "infrastructure"),
        "overall_pass_rate": rate([bool(item.get("overall_pass")) for item in executed]),
        "category_pass_rate": {
            category: rate(category_passes.get(category, []))
            for category in BENCHMARK_CATEGORIES
            if category_passes.get(category)
        },
        "route_accuracy": _rate_or_none([bool(item.get("checks", {}).get("route_pass")) for item in route_cases]),
        "retrieval_source_hit_rate": _rate_or_none([bool(item.get("checks", {}).get("retrieval_pass")) for item in retrieval_cases]),
        "source_coverage": _avg_or_none(source_coverages),
        "tool_selection_accuracy": None if rag_only and not tool_cases else _rate_or_none([bool(item.get("checks", {}).get("tool_pass")) for item in tool_cases]),
        "constraint_following_accuracy": None if rag_only and not constraint_cases else _rate_or_none(
            [bool(item.get("checks", {}).get("constraints_pass")) for item in constraint_cases]
        ),
        "forbidden_action_violation_rate": None if rag_only and not constraint_cases else _rate_or_none(
            [item in forbidden_violation_cases for item in constraint_cases]
        ),
        "final_answer_non_empty_rate": _rate_or_none([bool(item.get("final_answer_non_empty")) for item in final_answer_cases]),
        "groundedness_pass_rate": _rate_or_none([bool(item.get("checks", {}).get("grounded_pass")) for item in grounded_cases]),
        "unsupported_claim_rate": _rate_or_none([item in unsupported_cases for item in grounded_cases]),
        "required_fact_coverage": (required_matched / required_total) if required_total else 0.0,
        "rag_retrieval_cases": len(rag_retrieval_cases),
        "rag_grounding_cases": len(rag_grounding_cases),
        "rag_ranking_cases": len(rag_ranking_cases),
        "rag_table_cases": len(rag_table_cases),
        "rag_retrieval_hit_rate": _rate_or_none(
            [bool(item.get("rag_checks", {}).get("source_hit_at_k")) for item in rag_retrieval_cases]
        ),
        "rag_grounding_pass_rate": _rate_or_none(
            [bool(item.get("rag_checks", {}).get("grounded_pass")) for item in rag_grounding_cases]
        ),
        "rag_table_ready_rate": _rate_or_none(
            [bool(item.get("rag_checks", {}).get("table_ready")) for item in rag_table_cases]
        ),
        "rag_ranking_ready_rate": _rate_or_none(
            [bool(item.get("rag_checks", {}).get("ranking_ready")) for item in rag_ranking_cases]
        ),
        "judge_enabled_cases": len(judge_cases),
        "judge_executed_cases": len(judge_executed_cases),
        "judge_grounded_pass_rate": _rate_or_none(
            [bool(item.get("judge", {}).get("pass")) for item in judge_executed_cases]
        ),
        "judge_correctness_avg": _avg_or_none(
            [float(item.get("judge", {}).get("correctness_score", 0.0) or 0.0) for item in judge_executed_cases]
        ),
        "judge_unsupported_claim_rate": _rate_or_none(
            [item in judge_unsupported_cases for item in judge_executed_cases]
        ),
        "by_subtype": _aggregate_boolean_rate(executed, lambda item: item.get("subtype")),
        "by_question_type": _question_type_summary(case_results),
        "by_difficulty": _aggregate_boolean_rate(executed, lambda item: item.get("difficulty")),
        "by_modalities": _aggregate_modalities(executed),
        "infrastructure_skip_rate": (
            sum(1 for item in skipped if item.get("skip_kind") == "infrastructure") / len(case_results)
            if case_results
            else 0.0
        ),
        "skipped": [
            {
                "id": item["id"],
                "kind": item.get("skip_kind", "unknown"),
                "reason": item.get("skip_reason", ""),
            }
            for item in skipped
        ],
    }
