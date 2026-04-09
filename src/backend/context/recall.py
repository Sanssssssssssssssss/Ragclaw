from __future__ import annotations

import re
from typing import Any

from src.backend.context.models import ConversationRecallRecord
from src.backend.context.policies import conversation_query_for, should_use_conversation_recall
from src.backend.context.store import context_store


_TAG_PATTERN = re.compile(r"[A-Za-z0-9_./:-]{3,}")


def _tags_for(text: str) -> tuple[str, ...]:
    seen: list[str] = []
    for match in _TAG_PATTERN.finditer(str(text or "")):
        token = match.group(0).lower()
        if token not in seen:
            seen.append(token)
        if len(seen) >= 8:
            break
    return tuple(seen)


class ConversationRecallService:
    def record(self, *, state: dict[str, Any], updated_at: str) -> list[ConversationRecallRecord]:
        thread_id = str(state.get("thread_id", "") or state.get("session_id", "") or state.get("run_id", "") or "").strip()
        run_id = str(state.get("run_id", "") or "").strip()
        if not thread_id or not updated_at:
            return []
        history = list(state.get("history", []) or [])
        records: list[ConversationRecallRecord] = []
        for index, item in enumerate(history[-12:]):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip()
            content = str(item.get("content", "") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            record = context_store.insert_conversation_chunk(
                thread_id=thread_id,
                session_id=str(state.get("session_id", "") or "") or None,
                run_id=run_id,
                role=role,
                source_message_id=f"{thread_id}:{index}",
                snippet=content[:320],
                summary=content[:180],
                tags=_tags_for(content),
                metadata={"index": index},
                created_at=updated_at,
            )
            records.append(record)
        return records

    def should_recall(self, *, path_kind, state: dict[str, Any], history_trimmed: bool) -> bool:
        return should_use_conversation_recall(path_kind, state, history_trimmed=history_trimmed)

    def query_for(self, *, state: dict[str, Any], working_memory: dict[str, Any]) -> str:
        return conversation_query_for(state, working_memory)

    def retrieve(self, *, thread_id: str, query: str, limit: int = 3) -> list[ConversationRecallRecord]:
        return context_store.search_conversation_chunks(thread_id=thread_id, query=query, limit=limit)

    def list(self, *, thread_id: str, limit: int = 20) -> list[ConversationRecallRecord]:
        return context_store.list_conversation_chunks(thread_id=thread_id, limit=limit)


conversation_recall = ConversationRecallService()
