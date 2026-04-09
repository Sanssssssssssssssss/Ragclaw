from __future__ import annotations

from typing import Any

try:
    import tiktoken
except ImportError:  # pragma: no cover - local test fallback
    tiktoken = None

ENCODER = tiktoken.get_encoding("cl100k_base") if tiktoken is not None else None


def count_tokens(text: str) -> int:
    """Returns an integer token count from a text string input and estimates tokens with the shared encoder."""

    if ENCODER is None:
        return max(1, len(str(text or "").split())) if str(text or "").strip() else 0
    return len(ENCODER.encode(text or ""))


def count_message_usage(
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
    retrieval_steps: list[dict[str, Any]] | None = None,
) -> int:
    """Returns an integer token count from message parts inputs and estimates total usage for one assistant segment."""

    parts = [content or ""]
    for tool_call in tool_calls or []:
        parts.append(str(tool_call.get("tool", "")))
        parts.append(str(tool_call.get("input", "")))
        parts.append(str(tool_call.get("output", "")))
    for retrieval_step in retrieval_steps or []:
        parts.append(str(retrieval_step.get("title", "")))
        parts.append(str(retrieval_step.get("message", "")))
        for result in retrieval_step.get("results", []) or []:
            parts.append(str(result))
    return count_tokens("\n".join(part for part in parts if part))
