from __future__ import annotations

from typing import Any

__all__ = ["knowledge_indexer", "knowledge_orchestrator"]


def __getattr__(name: str) -> Any:
    if name == "knowledge_indexer":
        from knowledge_retrieval.indexer import knowledge_indexer

        return knowledge_indexer
    if name == "knowledge_orchestrator":
        from knowledge_retrieval.orchestrator import knowledge_orchestrator

        return knowledge_orchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
