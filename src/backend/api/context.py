from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.backend.context.consolidation import AutoDreamConsolidator
from src.backend.context.policies import project_namespace, thread_namespace, user_namespace
from src.backend.context.quarantine import ContextQuarantineService
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


def _namespaces_for(base_dir, thread_id: str) -> list[str]:
    return [user_namespace(), project_namespace(base_dir), thread_namespace(thread_id)]


def _quarantine_service_or_raise() -> ContextQuarantineService:
    base_dir = _base_dir_or_raise()
    session_manager = agent_manager.session_manager
    if session_manager is None:
        raise HTTPException(status_code=503, detail="Session manager is not initialized")
    try:
        runtime = agent_manager.get_harness_runtime()
        now_factory = runtime.now
    except Exception:  # pragma: no cover - defensive fallback for focused API tests
        now_factory = lambda: ""
    return ContextQuarantineService(
        session_manager=session_manager,
        base_dir=base_dir,
        now_factory=now_factory,
    )


@router.get("/context/sessions/{session_id}")
async def get_session_context(session_id: str) -> dict[str, Any]:
    base_dir = _base_dir_or_raise()
    thread_id = _thread_id_for(session_id)
    snapshot = context_store.get_thread_snapshot(thread_id=thread_id)
    namespaces = _namespaces_for(base_dir, thread_id)
    semantic = [item.to_dict() for item in semantic_memory.list(namespace=None, limit=40) if item.namespace in namespaces][:8]
    procedural = [item.to_dict() for item in procedural_memory.list(namespace=None, limit=40) if item.namespace in namespaces][:8]
    manifests = [item.to_dict() for item in context_store.list_memory_manifests(limit=80) if item.namespace in namespaces][:24]
    episodic = [item.to_dict() for item in context_store.list_memories(kind="episodic", namespace=thread_namespace(thread_id), limit=8)]
    assemblies = context_store.list_context_assemblies(thread_id=thread_id, limit=8)
    consolidation_runs = [item.to_dict() for item in context_store.list_consolidation_runs(thread_id=thread_namespace(thread_id), limit=6)]
    latest_consolidation = consolidation_runs[0] if consolidation_runs else None
    conversation_recall = [item.to_dict() for item in context_store.list_conversation_chunks(thread_id=thread_id, limit=8)]
    return {
        "session_id": session_id,
        "thread_id": thread_id,
        "working_memory": snapshot.working_memory if snapshot is not None else {},
        "episodic_summary": snapshot.episodic_summary if snapshot is not None else {},
        "session_memory_state": snapshot.session_memory_state if snapshot is not None else {},
        "semantic_memories": semantic,
        "procedural_memories": procedural,
        "episodic_memories": episodic,
        "manifests": manifests,
        "conversation_recall": conversation_recall,
        "assemblies": assemblies,
        "consolidation_runs": consolidation_runs,
        "latest_consolidation": latest_consolidation,
    }


@router.get("/context/sessions/{session_id}/turns")
async def list_context_turns(
    session_id: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    _base_dir_or_raise()
    thread_id = _thread_id_for(session_id)
    items = context_store.list_context_turn_snapshots(session_id=session_id, thread_id=thread_id, limit=limit)
    return {
        "session_id": session_id,
        "thread_id": thread_id,
        "items": [item.to_summary_dict() for item in items],
    }


@router.get("/context/sessions/{session_id}/turns/{turn_id}")
async def get_context_turn(session_id: str, turn_id: str) -> dict[str, Any]:
    _base_dir_or_raise()
    item = context_store.get_context_turn_snapshot(turn_id=turn_id, session_id=session_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Context turn not found")
    calls = context_store.list_context_model_calls(turn_id=turn_id)
    return {
        "session_id": session_id,
        "turn": item.to_dict(),
        "calls": [call.to_summary_dict() for call in calls],
        "audit_events": [
            event.to_dict()
            for event in context_store.list_context_events(thread_id=item.thread_id, limit=80)
            if event.turn_id == item.turn_id
        ],
    }


@router.get("/context/sessions/{session_id}/turns/{turn_id}/calls/{call_id}")
async def get_context_turn_call(session_id: str, turn_id: str, call_id: str) -> dict[str, Any]:
    _base_dir_or_raise()
    turn = context_store.get_context_turn_snapshot(turn_id=turn_id, session_id=session_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="Context turn not found")
    call = context_store.get_context_model_call(call_id=call_id, turn_id=turn_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Context model call not found")
    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "call": call.to_dict(),
        "turn": turn.to_summary_dict(),
    }


@router.post("/context/sessions/{session_id}/turns/{turn_id}/exclude")
async def exclude_context_turn(session_id: str, turn_id: str) -> dict[str, Any]:
    _base_dir_or_raise()
    try:
        result = _quarantine_service_or_raise().exclude_turn(session_id=session_id, turn_id=turn_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"result": result.to_dict()}


@router.get("/context/sessions/{session_id}/turns/{turn_id}/derived-memories")
async def get_turn_derived_memories(session_id: str, turn_id: str) -> dict[str, Any]:
    _base_dir_or_raise()
    try:
        return _quarantine_service_or_raise().derived_memories(session_id=session_id, turn_id=turn_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/context/sessions/{session_id}/turns/{turn_id}")
async def hard_delete_context_turn(
    session_id: str,
    turn_id: str,
    force: bool = Query(default=False),
) -> dict[str, Any]:
    _base_dir_or_raise()
    try:
        result = _quarantine_service_or_raise().hard_delete_turn(
            session_id=session_id,
            turn_id=turn_id,
            force=force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result.blocked_reason:
        raise HTTPException(status_code=409, detail=result.blocked_reason)
    return {"result": result.to_dict()}


@router.get("/context/memories")
async def list_context_memories(
    kind: str = Query(..., pattern="^(semantic|procedural|episodic)$"),
    namespace: str | None = Query(default=None),
    query: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    _base_dir_or_raise()
    if query:
        namespace_value = str(namespace or "").strip()
        namespaces = [namespace_value] if namespace_value else []
        items = context_store.search_memories(kind=kind, namespaces=namespaces, query=query, limit=limit)  # type: ignore[arg-type]
    else:
        items = context_store.list_memories(kind=kind, namespace=namespace, limit=limit)  # type: ignore[arg-type]
    return {"kind": kind, "namespace": namespace, "query": query or "", "items": [item.to_dict() for item in items]}


@router.get("/context/memories/{memory_id}")
async def get_context_memory(memory_id: str) -> dict[str, Any]:
    _base_dir_or_raise()
    item = context_store.get_memory(memory_id=memory_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"memory": item.to_dict()}


@router.get("/context/manifests")
async def list_memory_manifests(
    kind: str | None = Query(default=None, pattern="^(semantic|procedural|episodic)$"),
    namespace: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    _base_dir_or_raise()
    items = context_store.list_memory_manifests(kind=kind, namespace=namespace, status=status, limit=limit)  # type: ignore[arg-type]
    return {"kind": kind, "namespace": namespace, "status": status, "items": [item.to_dict() for item in items]}


@router.get("/context/manifests/search")
async def search_memory_manifests(
    query: str = Query(..., min_length=1),
    kind: str | None = Query(default=None, pattern="^(semantic|procedural|episodic)$"),
    namespace: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    _base_dir_or_raise()
    namespaces = [namespace] if namespace else []
    items = context_store.search_memory_manifests(kind=kind, namespaces=namespaces, query=query, limit=limit)  # type: ignore[arg-type]
    return {"kind": kind, "namespace": namespace, "query": query, "items": [item.to_dict() for item in items]}


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


@router.post("/context/consolidation")
async def trigger_context_consolidation(
    session_id: str | None = Query(default=None),
    thread_id: str | None = Query(default=None),
) -> dict[str, Any]:
    base_dir = _base_dir_or_raise()
    resolved_thread_id = thread_id or (_thread_id_for(session_id) if session_id else "")
    if not resolved_thread_id:
        raise HTTPException(status_code=400, detail="session_id or thread_id is required")
    consolidator = AutoDreamConsolidator(base_dir=base_dir)
    result = consolidator.consolidate(
        trigger="manual",
        thread_id=thread_namespace(resolved_thread_id),
        started_at=agent_manager.get_harness_runtime().now(),
        force=True,
    )
    return {"consolidation": result.to_dict()}


@router.get("/context/consolidations")
async def list_context_consolidations(
    session_id: str | None = Query(default=None),
    thread_id: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    _base_dir_or_raise()
    resolved_thread_id = thread_id or (_thread_id_for(session_id) if session_id else "")
    items = context_store.list_consolidation_runs(
        thread_id=thread_namespace(resolved_thread_id) if resolved_thread_id else None,
        limit=limit,
    )
    return {"thread_id": resolved_thread_id or None, "items": [item.to_dict() for item in items]}
