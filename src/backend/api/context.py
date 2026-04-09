from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.backend.context.policies import project_namespace, thread_namespace, user_namespace
from src.backend.context.procedural_memory import procedural_memory
from src.backend.context.semantic_memory import semantic_memory
from src.backend.context.store import context_store
from src.backend.runtime.agent_manager import agent_manager

router = APIRouter()


def _base_dir_or_raise():
    if agent_manager.base_dir is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")
    return agent_manager.base_dir


def _thread_id_for(session_id: str) -> str:
    from src.backend.orchestration.checkpointing import checkpoint_store  # pylint: disable=import-outside-toplevel

    return checkpoint_store.thread_id_for(session_id=session_id, run_id=session_id)


@router.get("/context/sessions/{session_id}")
async def get_session_context(session_id: str) -> dict[str, Any]:
    base_dir = _base_dir_or_raise()
    thread_id = _thread_id_for(session_id)
    snapshot = context_store.get_thread_snapshot(thread_id=thread_id)
    namespaces = [user_namespace(), project_namespace(base_dir), thread_namespace(thread_id)]
    semantic = [item.to_dict() for item in semantic_memory.list(namespace=None, limit=20) if item.namespace in namespaces][:8]
    procedural = [item.to_dict() for item in procedural_memory.list(namespace=None, limit=20) if item.namespace in namespaces][:8]
    assemblies = context_store.list_context_assemblies(thread_id=thread_id, limit=8)
    return {
        "session_id": session_id,
        "thread_id": thread_id,
        "working_memory": snapshot.working_memory if snapshot is not None else {},
        "episodic_summary": snapshot.episodic_summary if snapshot is not None else {},
        "semantic_memories": semantic,
        "procedural_memories": procedural,
        "assemblies": assemblies,
    }


@router.get("/context/memories")
async def list_context_memories(
    kind: str = Query(..., pattern="^(semantic|procedural)$"),
    namespace: str | None = Query(default=None),
    query: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    _base_dir_or_raise()
    if kind == "semantic":
        if query:
            namespace_value = str(namespace or "").strip()
            namespaces = [namespace_value] if namespace_value else []
            items = semantic_memory.search(namespaces=namespaces, query=query, limit=limit)
        else:
            items = semantic_memory.list(namespace=namespace, limit=limit)
    else:
        if query:
            namespace_value = str(namespace or "").strip()
            namespaces = [namespace_value] if namespace_value else []
            items = procedural_memory.search(namespaces=namespaces, query=query, limit=limit)
        else:
            items = procedural_memory.list(namespace=namespace, limit=limit)
    return {"kind": kind, "namespace": namespace, "query": query or "", "items": [item.to_dict() for item in items]}


@router.get("/context/assemblies")
async def list_context_assemblies(
    session_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    _base_dir_or_raise()
    thread_id = _thread_id_for(session_id) if session_id else None
    items = context_store.list_context_assemblies(thread_id=thread_id, run_id=run_id, limit=limit)
    return {"thread_id": thread_id, "run_id": run_id, "assemblies": items}
