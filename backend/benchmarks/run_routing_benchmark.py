from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graph.agent import AgentManager


DEFAULT_CASES_PATH = Path(__file__).resolve().with_name("routing_cases.json")
DEFAULT_OUTPUT_PATH = BACKEND_DIR / "storage" / "benchmarks" / "routing_benchmark_latest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the lightweight routing benchmark locally.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="Path to routing benchmark cases JSON.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="Path for the JSON result file.")
    parser.add_argument("--max-cases", type=int, default=0, help="Optional cap for quick iterations.")
    return parser.parse_args()


def _load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("routing benchmark cases must be a JSON list")
    return [dict(item) for item in payload]


def _expected_needs_tools(case: dict[str, Any]) -> bool:
    return bool(case.get("expected_allowed_tools", []))


def _expected_needs_retrieval(case: dict[str, Any]) -> bool:
    expected_route = str(case.get("expected_route", case.get("expected_intent", "")) or "").strip()
    return expected_route == "knowledge_qa"


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _infer_expected_subtype(case: dict[str, Any]) -> str:
    explicit = str(case.get("expected_subtype", "") or "").strip()
    if explicit:
        return explicit

    expected_route = str(case.get("expected_route", case.get("expected_intent", "")) or "").strip()
    expected_tools = tuple(case.get("expected_allowed_tools", []))
    case_id = str(case.get("id", "") or "").strip().lower()
    raw_input = str(case.get("input", "") or "")
    lowered = raw_input.lower()

    if expected_route == "workspace_file_ops":
        if expected_tools == ("read_file",):
            return "read_existing_file"
        if expected_tools == ("terminal",):
            if any(term in lowered for term in ("find", "search", "list", "where", "look in", "目录", "查找", "搜索", "列出", "定位")):
                return "search_workspace_file"
            return "search_workspace_file"
        if "run" in lowered or "modify" in lowered or "edit" in lowered or "patch" in lowered:
            return "modify_or_run_in_workspace"
        return "read_existing_file"

    if expected_route == "computation_or_transformation":
        if any(term in lowered for term in ("rewrite", "summarize", "translate", "改写", "总结", "翻译")):
            return "pure_text_transformation"
        if any(term in lowered for term in ("run this code", "execute this code", "run code", "execute code", "运行这段代码", "执行这段代码")):
            return "code_execution_request"
        if expected_tools == ("python_repl",):
            if any(
                term in lowered
                for term in (".json", ".csv", ".tsv", ".xlsx", ".xls", ".txt", ".py", "faq", "config", "file", "文件", "表格", "列", "字段")
            ) or case_id.startswith("compute_"):
                return "file_backed_calculation"
            return "pure_calculation"
        if not expected_tools:
            if any(term in lowered for term in ("rewrite", "summarize", "translate", "改写", "总结", "翻译")):
                return "pure_text_transformation"
            return "pure_calculation"
    return ""


def _tool_metrics(results: list[dict[str, Any]]) -> dict[str, float]:
    total = len(results)
    overexposed = sum(1 for item in results if item["tool_overexposed"])
    underselected = sum(1 for item in results if item["tool_underselected"])
    return {
        "whitelist_minimality": round(1.0 - _safe_rate(overexposed, total), 4) if total > 0 else 0.0,
        "tool_overexposure_rate": round(_safe_rate(overexposed, total), 4),
        "tool_underselection_rate": round(_safe_rate(underselected, total), 4),
    }


def _summarize_case_bucket(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    route_hits = sum(1 for item in results if item["route_match"])
    tool_hits = sum(1 for item in results if item["allowed_tools_match"])
    overroute = sum(1 for item in results if item["overroute"])
    underroute = sum(1 for item in results if item["underroute"])
    llm_router_cases = [item for item in results if str(item.get("source", "")).startswith("llm_router")]
    summary = {
        "total_cases": total,
        "route_accuracy": round(_safe_rate(route_hits, total), 4),
        "allowed_tools_accuracy": round(_safe_rate(tool_hits, total), 4),
        "overroute_rate": round(_safe_rate(overroute, total), 4),
        "underroute_rate": round(_safe_rate(underroute, total), 4),
        "llm_router_cases": len(llm_router_cases),
    }
    summary.update(_tool_metrics(results))
    return summary


async def _run() -> int:
    args = parse_args()
    cases = _load_cases(Path(args.cases))
    if args.max_cases > 0:
        cases = cases[: args.max_cases]

    manager = AgentManager()
    manager.initialize(BACKEND_DIR)

    results: list[dict[str, Any]] = []
    for case in cases:
        history = case.get("history", [])
        if not isinstance(history, list):
            history = []
        strategy, decision = await manager.resolve_routing(str(case.get("input", "")), history)
        expected_tools = tuple(case.get("expected_allowed_tools", []))
        expected_route = str(case.get("expected_route", case.get("expected_intent", "")) or "").strip()
        expected_subtype = _infer_expected_subtype(case)
        predicted_tools = tuple(decision.allowed_tools)
        predicted_subtype = str(getattr(decision, "subtype", "") or "")
        expected_needs_tools = _expected_needs_tools(case)
        expected_needs_retrieval = _expected_needs_retrieval(case)
        ambiguous = bool(case.get("ambiguous", False))
        full_match = decision.intent == expected_route and predicted_tools == expected_tools
        overroute = (decision.needs_tools and not expected_needs_tools) or (
            decision.needs_retrieval and not expected_needs_retrieval
        )
        underroute = (expected_needs_tools and not decision.needs_tools) or (
            expected_needs_retrieval and not decision.needs_retrieval
        )
        tool_overexposed = bool(set(predicted_tools) - set(expected_tools))
        tool_underselected = bool(set(expected_tools) - set(predicted_tools))
        results.append(
            {
                "id": case.get("id"),
                "bucket": case.get("bucket"),
                "input": case.get("input"),
                "expected_route": expected_route,
                "expected_subtype": expected_subtype,
                "predicted_intent": decision.intent,
                "predicted_subtype": predicted_subtype,
                "expected_allowed_tools": list(expected_tools),
                "predicted_allowed_tools": list(predicted_tools),
                "route_match": decision.intent == expected_route,
                "subtype_match": predicted_subtype == expected_subtype if expected_subtype else predicted_subtype == "",
                "allowed_tools_match": predicted_tools == expected_tools,
                "full_match": full_match,
                "overroute": overroute,
                "underroute": underroute,
                "tool_overexposed": tool_overexposed,
                "tool_underselected": tool_underselected,
                "source": decision.source,
                "confidence": decision.confidence,
                "reason_short": decision.reason_short,
                "prompt_tokens": decision.prompt_tokens,
                "output_tokens": decision.output_tokens,
                "ambiguous": ambiguous,
                "ambiguity_flags": list(decision.ambiguity_flags),
                "escalated": decision.escalated,
                "model_name": decision.model_name,
                "hard_constraints": {
                    "allow_tools": strategy.allow_tools,
                    "allow_knowledge": strategy.allow_knowledge,
                    "allow_retrieval": strategy.allow_retrieval,
                    "force_direct_answer": strategy.force_direct_answer,
                    "allowed_tools": sorted(strategy.allowed_tools),
                    "blocked_tools": sorted(strategy.blocked_tools),
                },
            }
        )

    total = len(results)
    route_hits = sum(1 for item in results if item["route_match"])
    tool_hits = sum(1 for item in results if item["allowed_tools_match"])
    overroute = sum(1 for item in results if item["overroute"])
    underroute = sum(1 for item in results if item["underroute"])
    llm_router_results = [item for item in results if str(item.get("source", "")).startswith("llm_router")]
    ambiguous_results = [item for item in results if item["ambiguous"]]
    escalated_results = [item for item in results if item["escalated"]]
    by_bucket_raw: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_intent_raw: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_subtype_raw: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        by_bucket_raw[str(item.get("bucket", "unknown"))].append(item)
        by_intent_raw[str(item.get("expected_route", "unknown"))].append(item)
        by_subtype_raw[str(item.get("expected_subtype", "") or "none")].append(item)

    summary = {
        "total_cases": total,
        "route_accuracy": round(_safe_rate(route_hits, total), 4),
        "allowed_tools_accuracy": round(_safe_rate(tool_hits, total), 4),
        "overroute_rate": round(_safe_rate(overroute, total), 4),
        "underroute_rate": round(_safe_rate(underroute, total), 4),
        "llm_router_cases": len(llm_router_results),
        "llm_router_prompt_tokens": sum(item["prompt_tokens"] for item in llm_router_results),
        "llm_router_output_tokens": sum(item["output_tokens"] for item in llm_router_results),
        "avg_llm_router_prompt_tokens": round(
            _safe_rate(sum(item["prompt_tokens"] for item in llm_router_results), len(llm_router_results)),
            2,
        ),
        "avg_llm_router_output_tokens": round(
            _safe_rate(sum(item["output_tokens"] for item in llm_router_results), len(llm_router_results)),
            2,
        ),
        "ambiguity_resolution_rate": round(
            _safe_rate(sum(1 for item in ambiguous_results if item["full_match"]), len(ambiguous_results)),
            4,
        ),
        "escalation_rate": round(_safe_rate(len(escalated_results), total), 4),
        "escalation_accuracy": round(
            _safe_rate(sum(1 for item in escalated_results if item["full_match"]), len(escalated_results)),
            4,
        ),
    }
    summary.update(_tool_metrics(results))

    payload = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "by_bucket": {bucket: _summarize_case_bucket(bucket_results) for bucket, bucket_results in by_bucket_raw.items()},
        "by_intent": {intent: _summarize_case_bucket(intent_results) for intent, intent_results in by_intent_raw.items()},
        "by_subtype": {subtype: _summarize_case_bucket(subtype_results) for subtype, subtype_results in by_subtype_raw.items()},
        "cases": results,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
