from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BENCHMARK_DIR = Path(__file__).resolve().parent
LEGACY_CASES_PATH = BENCHMARK_DIR / "cases.json"
RAG_CASE_DIR = BENCHMARK_DIR / "rag"
RAG_CASE_PATHS = {
    "retrieval": RAG_CASE_DIR / "retrieval_cases.json",
    "grounding": RAG_CASE_DIR / "grounding_cases.json",
    "ranking": RAG_CASE_DIR / "ranking_cases.json",
    "table": RAG_CASE_DIR / "table_cases.json",
}
VALID_SUITES = {"smoke", "full"}
VALID_MODULES = {"rag", "routing", "tool", "constraints", "groundedness"}
VALID_RAG_SUBTYPES = {"retrieval", "grounding", "ranking", "table"}
QUESTION_TYPES = (
    "direct_fact",
    "compare",
    "negation",
    "fuzzy",
    "multi_hop",
    "cross_file_aggregation",
)
SOURCE_PATH_FIELDS = ("gold_sources", "gold_chunks", "gold_tables")


@dataclass(frozen=True)
class BenchmarkSelection:
    suite: str | None = None
    module: str | None = None
    rag_subtype: str | None = None
    question_type: str | None = None
    difficulty_min: int | None = None
    difficulty_max: int | None = None
    modalities: tuple[str, ...] = ()
    sample_per_type: int | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "suite": self.suite,
            "module": self.module,
            "rag_subtype": self.rag_subtype,
            "question_type": self.question_type,
            "difficulty_min": self.difficulty_min,
            "difficulty_max": self.difficulty_max,
            "modalities": ",".join(self.modalities) if self.modalities else None,
            "sample_per_type": self.sample_per_type,
        }


def normalize_suite(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized not in VALID_SUITES:
        raise ValueError(f"Unsupported suite: {value}")
    return normalized


def normalize_module(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized not in VALID_MODULES:
        raise ValueError(f"Unsupported module: {value}")
    return normalized


def normalize_rag_subtype(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized not in VALID_RAG_SUBTYPES:
        raise ValueError(f"Unsupported rag subtype: {value}")
    return normalized


def normalize_question_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def normalize_modalities(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(
        sorted(
            {
                str(item).strip().lower()
                for item in str(value).split(",")
                if str(item).strip()
            }
        )
    )


def normalize_optional_int(value: int | str | None, *, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unsupported {field_name}: {value}") from exc


def _load_json_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Benchmark case file must contain a JSON list: {path}")
    return [_normalize_case(item) for item in payload if isinstance(item, dict)]


def _decode_hash_unicode(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16))

    return re.sub(r"#U([0-9A-Fa-f]{4})", replace, str(value))


def _normalize_source_path(value: str) -> str:
    normalized = _decode_hash_unicode(str(value).strip())
    if not normalized:
        return normalized
    normalized = normalized.replace("\\", "/")
    if "knowledge/" in normalized:
        normalized = normalized[normalized.find("knowledge/") :]
    if not normalized.startswith("knowledge/"):
        normalized = f"knowledge/{normalized.lstrip('/')}"
    return normalized


def normalize_source_path(value: str) -> str:
    return _normalize_source_path(value)


def _normalize_case(case: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(case)
    for field in SOURCE_PATH_FIELDS:
        values = normalized.get(field)
        if values is None:
            continue
        if isinstance(values, str):
            values = [values]
        normalized[field] = [_normalize_source_path(item) for item in values if str(item).strip()]

    modalities = normalized.get("modalities")
    if isinstance(modalities, str):
        normalized["modalities"] = [modalities]
    return normalized


def _matches_suite(case: dict[str, Any], suite: str) -> bool:
    suites = case.get("suites")
    if suites is None:
        return suite == "full"
    if isinstance(suites, str):
        suites = [suites]
    normalized = {str(item).strip().lower() for item in suites if str(item).strip()}
    return suite in normalized


def _case_modalities(case: dict[str, Any]) -> set[str]:
    raw_modalities = case.get("modalities", [])
    if isinstance(raw_modalities, str):
        raw_modalities = [raw_modalities]
    return {
        str(item).strip().lower()
        for item in raw_modalities
        if str(item).strip()
    }


def _matches_selection(case: dict[str, Any], selection: BenchmarkSelection) -> bool:
    if selection.question_type:
        case_question_type = str(case.get("question_type", "")).strip().lower()
        if case_question_type != selection.question_type:
            return False

    difficulty_value = case.get("difficulty")
    if selection.difficulty_min is not None or selection.difficulty_max is not None:
        try:
            difficulty = int(difficulty_value)
        except (TypeError, ValueError):
            return False
        if selection.difficulty_min is not None and difficulty < selection.difficulty_min:
            return False
        if selection.difficulty_max is not None and difficulty > selection.difficulty_max:
            return False

    if selection.modalities:
        case_modalities = _case_modalities(case)
        if not case_modalities or not set(selection.modalities).issubset(case_modalities):
            return False

    return True


def _sample_cases(cases: list[dict[str, Any]], sample_per_type: int | None) -> list[dict[str, Any]]:
    if sample_per_type is None or sample_per_type <= 0:
        return cases

    buckets: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        question_type = str(case.get("question_type", "untyped") or "untyped").strip().lower()
        buckets.setdefault(question_type, []).append(case)

    sampled: list[dict[str, Any]] = []
    for question_type in sorted(buckets):
        subtype_buckets: dict[str, list[dict[str, Any]]] = {}
        for case in buckets[question_type]:
            subtype = str(case.get("subtype", "") or "unknown").strip().lower() or "unknown"
            subtype_buckets.setdefault(subtype, []).append(case)

        selected_for_type: list[dict[str, Any]] = []
        subtype_order = [key for key in sorted(subtype_buckets)]
        while len(selected_for_type) < sample_per_type and subtype_order:
            next_round: list[str] = []
            for subtype in subtype_order:
                items = subtype_buckets.get(subtype, [])
                if items and len(selected_for_type) < sample_per_type:
                    selected_for_type.append(items.pop(0))
                if items:
                    next_round.append(subtype)
            subtype_order = next_round
        sampled.extend(selected_for_type)
    return sampled


def _dedupe_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for case in cases:
        case_id = str(case.get("id", "")).strip()
        if not case_id or case_id in seen_ids:
            continue
        seen_ids.add(case_id)
        deduped.append(case)
    return deduped


def _load_non_rag_cases() -> list[dict[str, Any]]:
    return _load_json_cases(LEGACY_CASES_PATH)


def _load_rag_cases(rag_subtype: str | None = None) -> list[dict[str, Any]]:
    if rag_subtype:
        return _load_json_cases(RAG_CASE_PATHS[rag_subtype])

    cases: list[dict[str, Any]] = []
    for subtype in ("retrieval", "grounding", "ranking", "table"):
        cases.extend(_load_json_cases(RAG_CASE_PATHS[subtype]))
    return cases


def load_cases(selection: BenchmarkSelection) -> list[dict[str, Any]]:
    if selection.module == "rag":
        cases = _dedupe_cases(_load_rag_cases(selection.rag_subtype))
        cases = [case for case in cases if _matches_selection(case, selection)]
        return _sample_cases(cases, selection.sample_per_type)

    if selection.module:
        cases = _dedupe_cases(
            [
                case
                for case in _load_non_rag_cases()
                if str(case.get("module", "")).strip().lower() == selection.module
            ]
        )
        cases = [case for case in cases if _matches_selection(case, selection)]
        return _sample_cases(cases, selection.sample_per_type)

    suite = selection.suite or "full"
    cases = [case for case in _load_non_rag_cases() if _matches_suite(case, suite)]
    rag_cases = [case for case in _load_rag_cases() if _matches_suite(case, suite)]
    combined = _dedupe_cases(cases + rag_cases)
    combined = [case for case in combined if _matches_selection(case, selection)]
    return _sample_cases(combined, selection.sample_per_type)
