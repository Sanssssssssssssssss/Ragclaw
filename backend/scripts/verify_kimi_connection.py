from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langchain_openai import ChatOpenAI

from src.backend.runtime.config import get_settings


def main() -> None:
    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit("Missing LLM API key in backend/.env.")

    client = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=0,
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
                "temperature": 0,
                "reply": str(content).strip(),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
