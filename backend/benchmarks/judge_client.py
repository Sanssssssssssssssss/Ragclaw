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

    def judge(self, prompt_payload: dict[str, Any]) -> dict[str, Any]:
        request_body = {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a strict RAG benchmark judge. "
                        "Return JSON only with keys grounded_score, correctness_score, "
                        "unsupported_claims, reasoning_summary, verdict."
                    ),
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
        parsed = _extract_json_block(str(content or ""))
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


def load_judge_client() -> JudgeClient | None:
    get_settings()
    base_url = (os.getenv("JUDGE_BASE_URL") or os.getenv("judge_base_url") or "").strip()
    api_key = (os.getenv("JUDGE_API_KEY") or os.getenv("judge_api_key") or "").strip()
    model = (os.getenv("JUDGE_MODEL") or os.getenv("judge_model") or "").strip()
    timeout_raw = (os.getenv("JUDGE_TIMEOUT_SECONDS") or os.getenv("judge_timeout_seconds") or "").strip()
    if not (base_url and api_key and model):
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
