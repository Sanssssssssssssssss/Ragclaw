from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_openai import ChatOpenAI

from config import get_settings


def main() -> None:
    settings = get_settings()
    if settings.llm_provider != "kimi":
        raise SystemExit(
            f"Expected LLM_PROVIDER=kimi, got {settings.llm_provider!r}. "
            "Please update backend/.env first."
        )
    if not settings.llm_api_key:
        raise SystemExit("Missing Kimi API key in backend/.env.")

    client = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=settings.llm_temperature,
    )
    response = client.invoke(
        [
            {
                "role": "system",
                "content": "You are a connectivity test assistant. Reply with exactly one short line.",
            },
            {
                "role": "user",
                "content": "Reply with: Kimi connection ok",
            },
        ]
    )
    content = getattr(response, "content", "")
    if isinstance(content, list):
        content = "".join(
            str(item.get("text", "")) for item in content if isinstance(item, dict)
        )

    print(
        json.dumps(
            {
                "provider": settings.llm_provider,
                "model": settings.llm_model,
                "base_url": settings.llm_base_url,
                "temperature": settings.llm_temperature,
                "reply": str(content).strip(),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
