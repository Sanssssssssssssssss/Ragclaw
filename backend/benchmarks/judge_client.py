from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

try:
    from ..config import get_settings
except ImportError:  # pragma: no cover - fallback for running inside backend cwd
    from config import get_settings


def _extract_json_block(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if not text:
        raise ValueError("Judge returned empty content")

    fenced_prefix = "```json"
    if fenced_prefix in text.lower():
        start = text.lower().find(fenced_prefix)
        end = text.rfind("```")
        if start != -1 and end != -1 and end > start:
            candidate = text[start + len(fenced_prefix) : end].strip()
            return json.loads(candidate)

    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        return json.loads(text[first : last + 1])
    return json.loads(text)


@dataclass(frozen=True)
class JudgeSettings:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int = 120
    temperature: float | None = None


class JudgeClient:
    def __init__(self, settings: JudgeSettings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.base_url.rstrip("/"),
            timeout=httpx.Timeout(self.settings.timeout_seconds),
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self.client.close()

    def _complete_json(
        self,
        *,
        system_prompt: str,
        prompt_payload: dict[str, Any],
    ) -> dict[str, Any]:
        request_body = {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt_payload, ensure_ascii=False, indent=2),
                },
            ],
        }
        if self.settings.temperature is not None:
            request_body["temperature"] = self.settings.temperature

        response = self.client.post(
            "/chat/completions",
            json=request_body,
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise ValueError("Judge response did not contain choices")
        content = (
            choices[0].get("message", {}).get("content")
            if isinstance(choices[0], dict)
            else None
        )
        return _extract_json_block(str(content or ""))

    def judge(self, prompt_payload: dict[str, Any]) -> dict[str, Any]:
        parsed = self._complete_json(
            system_prompt=(
                "You are a strict RAG benchmark judge. "
                "Return JSON only with keys grounded_score, correctness_score, "
                "unsupported_claims, reasoning_summary, verdict."
            ),
            prompt_payload=prompt_payload,
        )
        return {
            "grounded_score": float(parsed.get("grounded_score", 0.0) or 0.0),
            "correctness_score": float(parsed.get("correctness_score", 0.0) or 0.0),
            "unsupported_claims": [
                str(item)
                for item in parsed.get("unsupported_claims", [])
                if str(item).strip()
            ],
            "reasoning_summary": str(parsed.get("reasoning_summary", "") or "").strip(),
            "verdict": str(parsed.get("verdict", "") or "").strip().lower() or "unknown",
        }

    def judge_harness_case(self, prompt_payload: dict[str, Any]) -> dict[str, Any]:
        parsed = self._complete_json(
            system_prompt=(
                "You are a strict harness benchmark judge for an LLM-driven agent system. "
                "Evaluate whether the route, retrieval/tool usage, rewrite/planner behavior, "
                "and final answer were reasonable and honest. "
                "Return JSON only with keys: "
                "passed, score, reason, dimensions, details. "
                "Rules: score is 0 to 1. dimensions is an object of booleans. "
                "Use dimension keys from this set when applicable only: "
                "route_reasonable, retrieval_necessary, tool_necessary, "
                "rewrite_preserves_intent, planner_reasonable, grounded_answer, "
                "partiality_honest, conflicting_evidence_honesty, "
                "tool_or_evidence_reflection, unsupported_claim_control. "
                "details must be an object and may include commentary, unsupported_claims, rewrite_commentary, and notes. "
                "Judge rewrite/planner drift explicitly: preserve entities, time periods, metrics, constraints, and negation. "
                "Judge partiality honesty explicitly: if evidence is weak, conflicting, or incomplete, overconfident answers should fail. "
                "Be conservative: if evidence is weak or conflicting, mark overconfident answers as failing."
            ),
            prompt_payload=prompt_payload,
        )
        dimensions = parsed.get("dimensions", {})
        details = parsed.get("details", {})
        if not isinstance(dimensions, dict):
            dimensions = {}
        if not isinstance(details, dict):
            details = {}
        return {
            "passed": bool(parsed.get("passed", False)),
            "score": float(parsed.get("score", 0.0) or 0.0),
            "reason": str(parsed.get("reason", "") or "").strip(),
            "dimensions": {str(key): bool(value) for key, value in dimensions.items()},
            "details": details,
        }


def load_judge_client() -> JudgeClient | None:
    settings = get_settings()
    base_url = (os.getenv("JUDGE_BASE_URL") or os.getenv("judge_base_url") or "").strip()
    api_key = (os.getenv("JUDGE_API_KEY") or os.getenv("judge_api_key") or "").strip()
    model = (os.getenv("JUDGE_MODEL") or os.getenv("judge_model") or "").strip()
    timeout_raw = (os.getenv("JUDGE_TIMEOUT_SECONDS") or os.getenv("judge_timeout_seconds") or "").strip()
    if not (base_url and api_key and model):
        if settings.llm_api_key and settings.llm_base_url and settings.llm_model:
            base_url = settings.llm_base_url
            api_key = settings.llm_api_key
            model = settings.llm_model
        else:
            return None

    timeout_seconds = 120
    if timeout_raw:
        try:
            timeout_seconds = max(30, int(timeout_raw))
        except ValueError:
            timeout_seconds = 120
    temperature_raw = (os.getenv("JUDGE_TEMPERATURE") or os.getenv("judge_temperature") or "").strip()
    temperature: float | None
    if temperature_raw:
        try:
            temperature = float(temperature_raw)
        except ValueError:
            temperature = None
    elif "kimi-k2.5" in model.lower():
        temperature = 1.0
    else:
        temperature = 0.0
    return JudgeClient(
        JudgeSettings(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            temperature=temperature,
        )
    )
