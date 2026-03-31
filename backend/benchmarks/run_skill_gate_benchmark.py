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


DEFAULT_CASES_PATH = Path(__file__).resolve().with_name("skill_gate_cases.json")
DEFAULT_OUTPUT_PATH = BACKEND_DIR / "storage" / "benchmarks" / "skill_gate_benchmark_latest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the skill-gate benchmark locally.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="Path to skill benchmark cases JSON.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="Path for the JSON result file.")
    parser.add_argument("--max-cases", type=int, default=0, help="Optional cap for quick iterations.")
    return parser.parse_args()


def _load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("skill benchmark cases must be a JSON list")
    return [dict(item) for item in payload]


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _case_correct(case_result: dict[str, Any]) -> bool:
    if case_result["expected_use_skill"] != case_result["predicted_use_skill"]:
        return False
    if not case_result["expected_use_skill"]:
        return True
    return case_result["expected_skill_name"] == case_result["predicted_skill_name"]


def _skill_breakdown(results: list[dict[str, Any]], inventory: list[dict[str, Any]]) -> dict[str, Any]:
    by_skill: dict[str, dict[str, int]] = {
        item["skill_name"]: {
            "expected": 0,
            "predicted": 0,
            "correct": 0,
            "false_trigger": 0,
            "missed": 0,
        }
        for item in inventory
    }
    for result in results:
        expected_name = str(result.get("expected_skill_name", "") or "")
        predicted_name = str(result.get("predicted_skill_name", "") or "")
        expected_use = bool(result.get("expected_use_skill", False))
        predicted_use = bool(result.get("predicted_use_skill", False))

        if expected_use and expected_name in by_skill:
            by_skill[expected_name]["expected"] += 1
        if predicted_use and predicted_name in by_skill:
            by_skill[predicted_name]["predicted"] += 1
        if expected_use and predicted_use and expected_name == predicted_name and expected_name in by_skill:
            by_skill[expected_name]["correct"] += 1
        if predicted_use and (not expected_use or predicted_name != expected_name) and predicted_name in by_skill:
            by_skill[predicted_name]["false_trigger"] += 1
        if expected_use and (not predicted_use or predicted_name != expected_name) and expected_name in by_skill:
            by_skill[expected_name]["missed"] += 1
    return by_skill


async def _run() -> int:
    args = parse_args()
    cases = _load_cases(Path(args.cases))
    if args.max_cases > 0:
        cases = cases[: args.max_cases]

    manager = AgentManager()
    manager.initialize(BACKEND_DIR)
    inventory = manager._skill_gate.inventory()  # noqa: SLF001 - benchmark introspection only

    results: list[dict[str, Any]] = []
    by_route_raw: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for case in cases:
        history = case.get("history", [])
        if not isinstance(history, list):
            history = []
        strategy, routing_decision = await manager.resolve_routing(str(case.get("input", "")), history)
        skill_decision = manager.decide_skill(str(case.get("input", "")), history, strategy, routing_decision)
        result = {
            "id": case.get("id"),
            "bucket": case.get("bucket"),
            "input": case.get("input"),
            "expected_use_skill": bool(case.get("expected_use_skill", False)),
            "expected_skill_name": str(case.get("expected_skill_name", "") or ""),
            "predicted_use_skill": bool(skill_decision.use_skill),
            "predicted_skill_name": str(skill_decision.skill_name or ""),
            "expected_route": str(case.get("expected_route", "") or ""),
            "predicted_route": str(routing_decision.intent or ""),
            "expected_allowed_tools": list(case.get("expected_allowed_tools", [])),
            "predicted_allowed_tools": list(routing_decision.allowed_tools),
            "ambiguous": bool(case.get("ambiguous", False)),
            "reason_short": skill_decision.reason_short,
            "confidence": skill_decision.confidence,
            "skill_correct": False,
        }
        result["skill_correct"] = _case_correct(result)
        results.append(result)
        by_route_raw[result["expected_route"] or "unknown"].append(result)

    total = len(results)
    expected_positive = [item for item in results if item["expected_use_skill"]]
    predicted_positive = [item for item in results if item["predicted_use_skill"]]
    correct_positive = [
        item
        for item in results
        if item["expected_use_skill"] and item["predicted_use_skill"] and item["expected_skill_name"] == item["predicted_skill_name"]
    ]
    false_triggers = [item for item in results if item["predicted_use_skill"] and not item["expected_use_skill"]]
    missed = [item for item in results if item["expected_use_skill"] and not item["skill_correct"]]

    summary = {
        "total_cases": total,
        "skill_gate_accuracy": round(_safe_rate(sum(1 for item in results if item["skill_correct"]), total), 4),
        "skill_precision": round(_safe_rate(len(correct_positive), len(predicted_positive)), 4),
        "skill_recall": round(_safe_rate(len(correct_positive), len(expected_positive)), 4),
        "false_skill_trigger_rate": round(_safe_rate(len(false_triggers), total), 4),
        "missed_skill_rate": round(_safe_rate(len(missed), len(expected_positive)), 4),
    }

    payload = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "inventory": inventory,
        "summary": summary,
        "by_route": {
            route: {
                "total_cases": len(route_results),
                "skill_gate_accuracy": round(_safe_rate(sum(1 for item in route_results if item["skill_correct"]), len(route_results)), 4),
                "false_skill_trigger_rate": round(_safe_rate(sum(1 for item in route_results if item["predicted_use_skill"] and not item["expected_use_skill"]), len(route_results)), 4),
                "missed_skill_rate": round(_safe_rate(sum(1 for item in route_results if item["expected_use_skill"] and not item["skill_correct"]), sum(1 for item in route_results if item["expected_use_skill"])), 4),
            }
            for route, route_results in by_route_raw.items()
        },
        "by_skill": _skill_breakdown(results, inventory),
        "cases": results,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
