from __future__ import annotations

from typing import Any

try:
    from .judge_client import JudgeClient
except ImportError:  # pragma: no cover - fallback for running inside backend cwd
    from benchmarks.judge_client import JudgeClient


def should_run_judge(case: dict[str, Any]) -> bool:
    return (
        str(case.get("module", "")).strip().lower() == "rag"
        and bool(case.get("judge_enabled"))
    )


def evaluate_with_judge(
    case: dict[str, Any],
    trace: dict[str, Any],
    judge_client: JudgeClient | None,
) -> dict[str, Any]:
    if not should_run_judge(case):
        return {
            "requested": False,
            "configured": bool(judge_client),
            "skipped": True,
            "skip_reason": "Judge disabled for this case",
        }

    if judge_client is None:
        return {
            "requested": True,
            "configured": False,
            "skipped": True,
            "skip_reason": "Judge client is not configured",
        }

    prompt_payload = {
        "user_query": case.get("input", ""),
        "retrieved_sources": trace.get("retrieval_sources", []),
        "support_corpus": trace.get("support_corpus", ""),
        "final_answer": trace.get("final_answer", ""),
        "judge_expectations": case.get("judge_expectations") or {},
    }
    judged = judge_client.judge(prompt_payload)
    unsupported_claims = [
        str(item)
        for item in judged.get("unsupported_claims", [])
        if str(item).strip()
    ]
    grounded_score = float(judged.get("grounded_score", 0.0) or 0.0)
    correctness_score = float(judged.get("correctness_score", 0.0) or 0.0)
    verdict = str(judged.get("verdict", "") or "").strip().lower() or "unknown"
    return {
        "requested": True,
        "configured": True,
        "skipped": False,
        "grounded_score": grounded_score,
        "correctness_score": correctness_score,
        "unsupported_claims": unsupported_claims,
        "reasoning_summary": str(judged.get("reasoning_summary", "") or "").strip(),
        "verdict": verdict,
        "pass": verdict in {"pass", "supported"} and grounded_score >= 0.5,
        "unsupported": bool(unsupported_claims) or verdict == "unsupported",
    }
