from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain.agents import create_agent
from langchain_core.tools import tool

from config import get_settings
from graph.agent import agent_manager


@tool
def add_numbers(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


async def main() -> None:
    settings = get_settings()
    model = agent_manager._build_chat_model()
    agent = create_agent(
        model=model,
        tools=[add_numbers],
        system_prompt="You are a concise tool-using assistant.",
    )

    final_parts: list[str] = []
    async for mode, payload in agent.astream(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "Use the tool to add 2 and 3, then answer with the result only.",
                }
            ]
        },
        stream_mode=["messages", "updates"],
    ):
        if mode != "messages":
            continue
        chunk, metadata = payload
        if metadata.get("langgraph_node") != "model":
            continue
        content = getattr(chunk, "content", "")
        if isinstance(content, str):
            final_parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    final_parts.append(str(item.get("text", "")))

    print(
        json.dumps(
            {
                "provider": settings.llm_provider,
                "model": settings.llm_model,
                "base_url": settings.llm_base_url,
                "reply": "".join(final_parts).strip(),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
