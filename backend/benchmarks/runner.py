from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from config import get_settings

try:
    from .case_loader import (
        BenchmarkSelection,
        load_cases,
        normalize_module,
        normalize_modalities,
        normalize_optional_int,
        normalize_question_type,
        normalize_rag_subtype,
        normalize_suite,
    )
    from .evaluator import evaluate_case, summarize_results
    from .judge import evaluate_with_judge
    from .judge_client import load_judge_client
except ImportError:  # pragma: no cover - fallback for running inside backend cwd
    from benchmarks.case_loader import (
        BenchmarkSelection,
        load_cases,
        normalize_module,
        normalize_modalities,
        normalize_optional_int,
        normalize_question_type,
        normalize_rag_subtype,
        normalize_suite,
    )
    from benchmarks.evaluator import evaluate_case, summarize_results
    from benchmarks.judge import evaluate_with_judge
    from benchmarks.judge_client import load_judge_client


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_BASE_URL = "http://127.0.0.1:8015"
OUTPUT_DIR = BACKEND_DIR / "storage" / "benchmarks"
KNOWLEDGE_MANIFEST_PATH = BACKEND_DIR / "storage" / "knowledge" / "manifest.json"
DEFAULT_CASE_DELAY_SECONDS = 3.0
DEFAULT_RATE_LIMIT_RETRY_BASE_SECONDS = 4.0
DEFAULT_MAX_RATE_LIMIT_RETRIES = 2


def _normalized_text(value: str) -> str:
    return " ".join(str(value or "").lower().split())


class BenchmarkRunner:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: int = 120,
        keep_sessions: bool = False,
        case_delay_seconds: float = DEFAULT_CASE_DELAY_SECONDS,
        rate_limit_retry_base_seconds: float = DEFAULT_RATE_LIMIT_RETRY_BASE_SECONDS,
        max_rate_limit_retries: int = DEFAULT_MAX_RATE_LIMIT_RETRIES,
        selection: BenchmarkSelection | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.keep_sessions = keep_sessions
        self.case_delay_seconds = max(0.0, float(case_delay_seconds))
        self.rate_limit_retry_base_seconds = max(0.0, float(rate_limit_retry_base_seconds))
        self.max_rate_limit_retries = max(0, int(max_rate_limit_retries))
        self.selection = selection or BenchmarkSelection(suite="full")
        self.client = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout_seconds))
        self.judge_client = load_judge_client()

    def _embeddings_expected(self) -> bool:
        settings = get_settings()
        return settings.embedding_provider == "local" or bool(settings.embedding_api_key)

    def close(self) -> None:
        self.client.close()
        if self.judge_client is not None:
            self.judge_client.close()

    def wait_for_health(self, *, timeout_seconds: int = 90) -> None:
        deadline = time.time() + timeout_seconds
        last_error = "backend health check did not return successfully"
        while time.time() < deadline:
            try:
                response = self.client.get("/health")
                response.raise_for_status()
                payload = response.json()
                if payload.get("status") == "ok":
                    return
                last_error = f"unexpected health payload: {payload}"
            except Exception as exc:  # pragma: no cover - runtime HTTP variation
                last_error = str(exc)
            time.sleep(1)
        raise RuntimeError(last_error)

    def rebuild_knowledge_index(self, *, timeout_seconds: int = 900) -> dict[str, Any]:
        response = self.client.post("/api/knowledge/index/rebuild")
        response.raise_for_status()

        deadline = time.time() + timeout_seconds
        last_status: dict[str, Any] = {}
        require_vector = self._embeddings_expected()
        while time.time() < deadline:
            status_response = self.client.get("/api/knowledge/index/status")
            status_response.raise_for_status()
            last_status = status_response.json()
            if require_vector and not last_status.get("building") and last_status.get("vector_error"):
                raise RuntimeError(
                    "knowledge index rebuild finished without vector readiness: "
                    f"{last_status.get('vector_error')}"
                )
            if (
                last_status.get("ready")
                and not last_status.get("building")
                and (
                    last_status.get("vector_ready")
                    if require_vector
                    else (last_status.get("vector_ready") or last_status.get("bm25_ready"))
                )
            ):
                return last_status
            time.sleep(2)
        raise RuntimeError(f"knowledge index rebuild timed out; last status={last_status}")

    def get_knowledge_index_status(self) -> dict[str, Any]:
        response = self.client.get("/api/knowledge/index/status")
        response.raise_for_status()
        return response.json()

    def indexed_source_types(self) -> set[str]:
        if not KNOWLEDGE_MANIFEST_PATH.exists():
            return set()
        payload = json.loads(KNOWLEDGE_MANIFEST_PATH.read_text(encoding="utf-8"))
        counts = Counter()
        for item in payload.get("documents", []):
            source_type = str(item.get("source_type", "")).strip().lower()
            if source_type:
                counts[source_type] += 1
        return set(counts)

    def manifest_source_type_counts(self) -> dict[str, int]:
        if not KNOWLEDGE_MANIFEST_PATH.exists():
            return {}
        payload = json.loads(KNOWLEDGE_MANIFEST_PATH.read_text(encoding="utf-8"))
        counts = Counter()
        for item in payload.get("documents", []):
            source_type = str(item.get("source_type", "")).strip().lower()
            if source_type:
                counts[source_type] += 1
        return dict(sorted(counts.items()))

    def create_session(self, title: str) -> str:
        response = self.client.post("/api/sessions", json={"title": title})
        response.raise_for_status()
        return str(response.json()["id"])

    def delete_session(self, session_id: str) -> None:
        response = self.client.delete(f"/api/sessions/{session_id}")
        response.raise_for_status()

    def fetch_history(self, session_id: str) -> dict[str, Any]:
        response = self.client.get(f"/api/sessions/{session_id}/history")
        response.raise_for_status()
        return response.json()

    def _parse_sse_events(self, response: httpx.Response) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        current_event: str | None = None
        current_data: list[str] = []

        def flush() -> None:
            nonlocal current_event, current_data
            if current_event is None:
                current_data = []
                return
            payload: dict[str, Any] = {}
            if current_data:
                raw = "\n".join(current_data)
                payload = json.loads(raw)
            events.append({"type": current_event, **payload})
            current_event = None
            current_data = []

        for line in response.iter_lines():
            if line == "":
                flush()
                continue
            if line.startswith("event: "):
                current_event = line[len("event: ") :]
                continue
            if line.startswith("data: "):
                current_data.append(line[len("data: ") :])
        flush()
        return events

    def send_message(self, session_id: str, message: str, *, stream: bool = True) -> list[dict[str, Any]]:
        with self.client.stream(
            "POST",
            "/api/chat",
            json={"message": message, "session_id": session_id, "stream": stream},
        ) as response:
            response.raise_for_status()
            return self._parse_sse_events(response)

    def build_trace(self, session_id: str, events: list[dict[str, Any]], history: dict[str, Any]) -> dict[str, Any]:
        assistant_messages = [item for item in history.get("messages", []) if item.get("role") == "assistant"]
        tool_calls = []
        retrieval_steps = []
        final_segments: list[str] = []
        for item in assistant_messages:
            content = str(item.get("content", "") or "").strip()
            if content:
                final_segments.append(content)
            tool_calls.extend(item.get("tool_calls", []) or [])
            retrieval_steps.extend(item.get("retrieval_steps", []) or [])

        called_tools: list[str] = []
        seen_tools: set[str] = set()
        for tool_call in tool_calls:
            tool_name = str(tool_call.get("tool", "")).strip()
            if tool_name and tool_name not in seen_tools:
                seen_tools.add(tool_name)
                called_tools.append(tool_name)

        retrieval_sources: list[str] = []
        seen_sources: set[str] = set()
        retrieval_snippets: list[str] = []
        knowledge_used = False
        memory_used = False
        prioritized_steps = list(retrieval_steps)
        final_knowledge_step_index: int | None = None
        for index, step in enumerate(retrieval_steps):
            if str(step.get("kind", "")).strip().lower() == "knowledge" and (step.get("results") or []):
                final_knowledge_step_index = index
        if final_knowledge_step_index is not None:
            prioritized_steps = [retrieval_steps[final_knowledge_step_index]] + [
                step for index, step in enumerate(retrieval_steps) if index != final_knowledge_step_index
            ]

        for step in prioritized_steps:
            kind = str(step.get("kind", "")).strip().lower()
            if kind == "knowledge":
                knowledge_used = True
            if kind == "memory":
                memory_used = True
            for result in step.get("results", []) or []:
                source_path = str(result.get("source_path", "")).strip()
                snippet = str(result.get("snippet", "")).strip()
                if snippet:
                    retrieval_snippets.append(snippet)
                if source_path and source_path not in seen_sources:
                    seen_sources.add(source_path)
                    retrieval_sources.append(source_path)

        if knowledge_used:
            detected_route = "knowledge"
        elif called_tools:
            detected_route = "tool"
        elif memory_used:
            detected_route = "memory"
        else:
            detected_route = "direct_answer"

        tool_outputs = [
            str(tool_call.get("output", "")).strip()
            for tool_call in tool_calls
            if str(tool_call.get("output", "")).strip()
        ]
        final_answer = "\n\n".join(final_segments).strip()
        done_events = [item for item in events if item.get("type") == "done"]
        error_events = [item for item in events if item.get("type") == "error"]
        if not final_answer and done_events:
            final_answer = str(done_events[-1].get("content", "") or "").strip()

        return {
            "session_id": session_id,
            "detected_route": detected_route,
            "called_tools": called_tools,
            "knowledge_used": knowledge_used,
            "memory_used": memory_used,
            "retrieval_sources": retrieval_sources,
            "retrieval_steps": retrieval_steps,
            "tool_calls": tool_calls,
            "tool_outputs": tool_outputs,
            "support_corpus": "\n\n".join(retrieval_snippets + tool_outputs),
            "final_answer": final_answer,
            "error_message": str(error_events[-1].get("error", "") or "").strip() if error_events else "",
            "events": events,
            "history": history,
        }

    def _rate_limit_error(self, trace: dict[str, Any]) -> str:
        combined = _normalized_text(
            "\n".join(
                [
                    str(trace.get("error_message", "") or ""),
                    str(trace.get("final_answer", "") or ""),
                ]
            )
        )
        if not combined:
            return ""

        patterns = (
            "rate limit",
            "rate_limit",
            "max rpm",
            "429",
            "too many requests",
            "try again after",
        )
        if any(pattern in combined for pattern in patterns):
            return str(trace.get("error_message", "") or trace.get("final_answer", "") or "").strip()
        return ""

    def _rate_limit_backoff_seconds(self, error_text: str, attempt_index: int) -> float:
        retry_after_seconds = 0.0
        match = re.search(r"try again after\s+(\d+(?:\.\d+)?)\s*seconds?", error_text, flags=re.IGNORECASE)
        if match:
            retry_after_seconds = float(match.group(1))
        exponential_backoff = self.rate_limit_retry_base_seconds * (2 ** attempt_index)
        return max(retry_after_seconds + 1.0, exponential_backoff, 2.0)

    def run_case(self, case: dict[str, Any], indexed_types: set[str]) -> dict[str, Any]:
        attempt_index = 0
        while True:
            session_id = self.create_session(f"benchmark-{case['id']}-{uuid.uuid4().hex[:8]}")
            trace: dict[str, Any] | None = None
            try:
                for item in case.get("history", []) or []:
                    if isinstance(item, dict):
                        message = str(item.get("content", "") or "")
                    else:
                        message = str(item)
                    if message.strip():
                        self.send_message(session_id, message)

                events = self.send_message(session_id, str(case["input"]))
                history = self.fetch_history(session_id)
                trace = self.build_trace(session_id, events, history)
            finally:
                if not self.keep_sessions:
                    self.delete_session(session_id)

            if trace is None:
                raise RuntimeError(f"Benchmark case {case['id']} did not produce a trace")

            rate_limit_error = self._rate_limit_error(trace)
            if rate_limit_error and attempt_index < self.max_rate_limit_retries:
                wait_seconds = self._rate_limit_backoff_seconds(rate_limit_error, attempt_index)
                print(
                    f"[benchmark] case={case['id']} hit rate limit, retrying in {wait_seconds:.1f}s "
                    f"(attempt {attempt_index + 1}/{self.max_rate_limit_retries})"
                )
                time.sleep(wait_seconds)
                attempt_index += 1
                continue

            result = evaluate_case(case, trace, indexed_types)
            result["retry_count"] = attempt_index
            if result.get("skipped"):
                result["judge"] = {
                    "requested": bool(case.get("judge_enabled")),
                    "configured": self.judge_client is not None,
                    "skipped": True,
                    "skip_reason": "Primary benchmark case was skipped",
                }
            else:
                result["judge"] = evaluate_with_judge(case, trace, self.judge_client)
            result["trace"] = {
                "detected_route": trace["detected_route"],
                "called_tools": trace["called_tools"],
                "retrieval_sources": trace["retrieval_sources"],
                "final_answer": trace["final_answer"],
                "error_message": trace.get("error_message", ""),
            }
            return result

    def run(self) -> dict[str, Any]:
        self.wait_for_health()
        cases = load_cases(self.selection)
        needs_knowledge_index = any(
            case.get("gold_sources")
            or case.get("required_source_types")
            or str(case.get("expected_route", "")).strip().lower() == "knowledge"
            or str(case.get("module", "")).strip().lower() == "rag"
            for case in cases
        )
        if needs_knowledge_index:
            index_status = self.rebuild_knowledge_index()
            indexed_types = self.indexed_source_types()
        else:
            index_status = self.get_knowledge_index_status()
            indexed_types = self.indexed_source_types()
        results: list[dict[str, Any]] = []
        for index, case in enumerate(cases):
            results.append(self.run_case(case, indexed_types))
            if index < len(cases) - 1 and self.case_delay_seconds > 0:
                print(
                    f"[benchmark] sleeping {self.case_delay_seconds:.1f}s before next case "
                    f"({index + 1}/{len(cases)})"
                )
                time.sleep(self.case_delay_seconds)
        summary = summarize_results(results)
        summary.update(self.selection.to_dict())
        started_at = datetime.now(timezone.utc).isoformat()
        payload = {
            "started_at": started_at,
            "base_url": self.base_url,
            "selection": self.selection.to_dict(),
            "runner_settings": {
                "case_delay_seconds": self.case_delay_seconds,
                "rate_limit_retry_base_seconds": self.rate_limit_retry_base_seconds,
                "max_rate_limit_retries": self.max_rate_limit_retries,
            },
            "judge": {
                "configured": self.judge_client is not None,
            },
            "knowledge_index_status": index_status,
            "manifest_source_type_counts": self.manifest_source_type_counts(),
            "indexed_source_types": sorted(indexed_types),
            "summary": summary,
            "cases": results,
        }
        return payload


def write_results(payload: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result_path = output_dir / f"benchmark-results-{timestamp}.json"
    latest_path = output_dir / "latest.json"
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    result_path.write_text(content, encoding="utf-8")
    latest_path.write_text(content, encoding="utf-8")
    return result_path


def print_summary(payload: dict[str, Any], result_path: Path) -> None:
    summary = payload["summary"]
    selection = payload.get("selection", {})

    def format_rate(value: Any) -> str:
        if value is None:
            return "n/a"
        return f"{float(value):.1%}"

    def format_avg(value: Any) -> str:
        if value is None:
            return "n/a"
        return f"{float(value):.2f}"

    print(f"[benchmark] Results saved to {result_path}")
    print(
        "[benchmark] suite={} module={} rag_subtype={} judge_configured={}".format(
            selection.get("suite") or "none",
            selection.get("module") or "none",
            selection.get("rag_subtype") or "none",
            bool(payload.get("judge", {}).get("configured")),
        )
    )
    print(
        "[benchmark] question_type={} difficulty_min={} difficulty_max={} modalities={} sample_per_type={}".format(
            selection.get("question_type") or "none",
            selection.get("difficulty_min") if selection.get("difficulty_min") is not None else "none",
            selection.get("difficulty_max") if selection.get("difficulty_max") is not None else "none",
            selection.get("modalities") or "none",
            selection.get("sample_per_type") if selection.get("sample_per_type") is not None else "none",
        )
    )
    print(
        "[benchmark] total_cases={} executed_cases={} skipped_cases={}".format(
            summary.get("total_cases", 0),
            summary.get("executed_cases", 0),
            summary.get("skipped_cases", 0),
        )
    )
    print(
        "[benchmark] overall_pass_rate={} route_accuracy={} retrieval_source_hit_rate={} "
        "tool_selection_accuracy={} constraint_following_accuracy={} "
        "final_answer_non_empty_rate={} groundedness_pass_rate={} infrastructure_skip_rate={}".format(
            format_rate(summary.get("overall_pass_rate", 0.0)),
            format_rate(summary.get("route_accuracy")),
            format_rate(summary.get("retrieval_source_hit_rate")),
            format_rate(summary.get("tool_selection_accuracy")),
            format_rate(summary.get("constraint_following_accuracy")),
            format_rate(summary.get("final_answer_non_empty_rate")),
            format_rate(summary.get("groundedness_pass_rate")),
            format_rate(summary.get("infrastructure_skip_rate", 0.0)),
        )
    )
    print(
        "[benchmark] rag_retrieval_hit_rate={} source_coverage={} rag_grounding_pass_rate={} "
        "required_fact_coverage={} judge_grounded_pass_rate={} "
        "judge_correctness_avg={}".format(
            format_rate(summary.get("rag_retrieval_hit_rate")),
            format_rate(summary.get("source_coverage")),
            format_rate(summary.get("rag_grounding_pass_rate")),
            format_rate(summary.get("required_fact_coverage", 0.0)),
            format_rate(summary.get("judge_grounded_pass_rate")),
            format_avg(summary.get("judge_correctness_avg")),
        )
    )
    category_rates = summary.get("category_pass_rate", {})
    if category_rates:
        print("[benchmark] category_pass_rate=" + json.dumps(category_rates, ensure_ascii=False))
    question_type_summary = summary.get("by_question_type", {})
    if question_type_summary:
        print("[benchmark] by_question_type=" + json.dumps(question_type_summary, ensure_ascii=False))
    if summary.get("skipped"):
        print("[benchmark] skipped=" + json.dumps(summary["skipped"], ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run backend-only benchmarks against the local API.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Backend base URL, e.g. http://127.0.0.1:8015")
    parser.add_argument("--suite", default="full", help="Benchmark suite to run: smoke or full.")
    parser.add_argument("--module", default=None, help="Optional module to run: rag, routing, tool, constraints, groundedness.")
    parser.add_argument("--rag-subtype", default=None, help="Optional rag subtype: retrieval, grounding, ranking, table.")
    parser.add_argument("--question-type", default=None, help="Optional question type filter, e.g. compare, negation, fuzzy.")
    parser.add_argument("--difficulty-min", default=None, help="Optional minimum difficulty filter.")
    parser.add_argument("--difficulty-max", default=None, help="Optional maximum difficulty filter.")
    parser.add_argument("--modalities", default=None, help="Optional comma-separated modalities filter, e.g. pdf,xlsx.")
    parser.add_argument("--sample-per-type", default=None, help="Optional max number of cases per question_type bucket.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Directory for benchmark result JSON files.")
    parser.add_argument("--keep-sessions", action="store_true", help="Keep benchmark sessions on disk instead of deleting them.")
    parser.add_argument(
        "--case-delay-seconds",
        type=float,
        default=DEFAULT_CASE_DELAY_SECONDS,
        help="Sleep between benchmark cases to reduce backend/provider request bursts.",
    )
    parser.add_argument(
        "--rate-limit-retry-base-seconds",
        type=float,
        default=DEFAULT_RATE_LIMIT_RETRY_BASE_SECONDS,
        help="Base delay for exponential backoff after provider rate-limit errors.",
    )
    parser.add_argument(
        "--max-rate-limit-retries",
        type=int,
        default=DEFAULT_MAX_RATE_LIMIT_RETRIES,
        help="Maximum retries for a benchmark case when the backend returns provider rate-limit errors.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suite = normalize_suite(args.suite)
    module = normalize_module(args.module)
    rag_subtype = normalize_rag_subtype(args.rag_subtype)
    question_type = normalize_question_type(args.question_type)
    difficulty_min = normalize_optional_int(args.difficulty_min, field_name="difficulty_min")
    difficulty_max = normalize_optional_int(args.difficulty_max, field_name="difficulty_max")
    sample_per_type = normalize_optional_int(args.sample_per_type, field_name="sample_per_type")
    modalities = normalize_modalities(args.modalities)
    if rag_subtype and module != "rag":
        raise SystemExit("--rag-subtype can only be used together with --module rag")
    if difficulty_min is not None and difficulty_max is not None and difficulty_min > difficulty_max:
        raise SystemExit("--difficulty-min cannot be greater than --difficulty-max")
    if module is not None:
        suite = None
    selection = BenchmarkSelection(
        suite=suite,
        module=module,
        rag_subtype=rag_subtype,
        question_type=question_type,
        difficulty_min=difficulty_min,
        difficulty_max=difficulty_max,
        modalities=modalities,
        sample_per_type=sample_per_type,
    )
    runner = BenchmarkRunner(
        args.base_url,
        keep_sessions=args.keep_sessions,
        case_delay_seconds=args.case_delay_seconds,
        rate_limit_retry_base_seconds=args.rate_limit_retry_base_seconds,
        max_rate_limit_retries=args.max_rate_limit_retries,
        selection=selection,
    )
    try:
        payload = runner.run()
    finally:
        runner.close()
    result_path = write_results(payload, Path(args.output_dir))
    print_summary(payload, result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
