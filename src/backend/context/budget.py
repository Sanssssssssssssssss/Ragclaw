from __future__ import annotations

from typing import Iterable

from src.backend.context.models import ContextPathKind, SlotBudget
from src.backend.runtime.token_utils import count_tokens


DEFAULT_EXCLUDED_FROM_PROMPT: tuple[str, ...] = (
    "raw trace events",
    "raw HITL audit payloads",
    "raw checkpoint blobs",
    "governor snapshots",
    "capability governance internals",
)


def budget_for_path(path_kind: ContextPathKind) -> SlotBudget:
    if path_kind == "knowledge_qa":
        return SlotBudget(
            system=1800,
            recent_history=700,
            working_memory=500,
            episodic_summary=450,
            semantic_memory=450,
            procedural_memory=450,
            artifacts=250,
            retrieval_evidence=1800,
            answer_reserve=700,
        )
    if path_kind == "capability_path":
        return SlotBudget(
            system=1800,
            recent_history=800,
            working_memory=550,
            episodic_summary=450,
            semantic_memory=350,
            procedural_memory=500,
            artifacts=1200,
            retrieval_evidence=350,
            answer_reserve=700,
        )
    if path_kind == "resumed_hitl":
        return SlotBudget(
            system=1800,
            recent_history=500,
            working_memory=750,
            episodic_summary=700,
            semantic_memory=450,
            procedural_memory=650,
            artifacts=900,
            retrieval_evidence=400,
            answer_reserve=700,
        )
    if path_kind == "recovery_path":
        return SlotBudget(
            system=1800,
            recent_history=500,
            working_memory=700,
            episodic_summary=600,
            semantic_memory=300,
            procedural_memory=450,
            artifacts=1000,
            retrieval_evidence=350,
            answer_reserve=700,
        )
    return SlotBudget(
        system=1800,
        recent_history=900,
        working_memory=500,
        episodic_summary=500,
        semantic_memory=300,
        procedural_memory=450,
        artifacts=300,
        retrieval_evidence=250,
        answer_reserve=700,
    )


def trim_text_to_budget(text: str, max_tokens: int) -> str:
    normalized = str(text or "").strip()
    if not normalized or max_tokens <= 0:
        return ""
    if count_tokens(normalized) <= max_tokens:
        return normalized

    lines = [line.rstrip() for line in normalized.splitlines() if line.strip()]
    kept: list[str] = []
    for line in lines:
        candidate = "\n".join(kept + [line]).strip()
        if candidate and count_tokens(candidate) > max_tokens:
            break
        kept.append(line)
    if kept:
        truncated = "\n".join(kept).strip()
        if count_tokens(truncated) <= max_tokens:
            return truncated

    words = normalized.split()
    compact: list[str] = []
    for word in words:
        candidate = (" ".join(compact + [word])).strip()
        if candidate and count_tokens(candidate) > max_tokens:
            break
        compact.append(word)
    return " ".join(compact).strip()


def trim_messages_to_budget(messages: Iterable[dict[str, str]], max_tokens: int) -> list[dict[str, str]]:
    if max_tokens <= 0:
        return []
    kept: list[dict[str, str]] = []
    running_tokens = 0
    for item in reversed(list(messages)):
        content = str(item.get("content", "") or "").strip()
        role = str(item.get("role", "") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        message_tokens = count_tokens(f"{role}: {content}")
        if kept and running_tokens + message_tokens > max_tokens:
            break
        if not kept and message_tokens > max_tokens:
            trimmed = trim_text_to_budget(content, max_tokens)
            if trimmed:
                kept.append({"role": role, "content": trimmed})
            break
        kept.append({"role": role, "content": content})
        running_tokens += message_tokens
    kept.reverse()
    return kept
