from __future__ import annotations

from typing import Any

from src.backend.context.models import StoredMemory
from src.backend.context.store import context_store


class SemanticMemoryService:
    def insert(
        self,
        *,
        namespace: str,
        title: str,
        content: str,
        summary: str = "",
        tags: list[str] | tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
        source: str = "",
        created_at: str,
        fingerprint: str,
    ) -> StoredMemory:
        return context_store.insert_memory(
            kind="semantic",
            namespace=namespace,
            title=title,
            content=content,
            summary=summary,
            tags=tags,
            metadata=metadata,
            source=source,
            created_at=created_at,
            fingerprint=fingerprint,
        )

    def search(self, *, namespaces: list[str] | tuple[str, ...], query: str, limit: int = 5) -> list[StoredMemory]:
        return context_store.search_memories(kind="semantic", namespaces=namespaces, query=query, limit=limit)

    def list(self, *, namespace: str | None = None, limit: int = 20) -> list[StoredMemory]:
        return context_store.list_memories(kind="semantic", namespace=namespace, limit=limit)


semantic_memory = SemanticMemoryService()
