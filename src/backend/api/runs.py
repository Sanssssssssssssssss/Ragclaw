from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.backend.runtime.agent_manager import agent_manager

router = APIRouter()


def _trace_repository():
    if agent_manager.runtime_backends is None:
        raise HTTPException(status_code=503, detail="runtime backends are not initialized")
    return agent_manager.runtime_backends.trace_repository


def _hitl_repository():
    if agent_manager.hitl_repository is None:
        raise HTTPException(status_code=503, detail="hitl repository is not initialized")
    return agent_manager.hitl_repository


@router.get("/runs")
async def list_runs(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    repository = _trace_repository()
    list_method = getattr(repository, "list_runs", None)
    if not callable(list_method):
        raise HTTPException(status_code=501, detail="run listing is not supported by current trace backend")
    items = list_method(limit=limit, offset=offset, session_id=session_id, status=status)
    return {"items": items, "count": len(items), "limit": limit, "offset": offset}


@router.get("/runs/stats")
async def run_stats() -> dict[str, Any]:
    repository = _trace_repository()
    stats_method = getattr(repository, "stats", None)
    if not callable(stats_method):
        raise HTTPException(status_code=501, detail="run stats are not supported by current trace backend")
    return stats_method()


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    repository = _trace_repository()
    try:
        trace = repository.read_trace(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    parity_method = getattr(repository, "parity_report", None)
    parity = parity_method(run_id) if callable(parity_method) else None
    return {
        "trace": trace,
        "parity": parity,
    }


@router.get("/runs/{run_id}/events")
async def get_run_events(
    run_id: str,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    repository = _trace_repository()
    events_method = getattr(repository, "list_run_events", None)
    if callable(events_method):
        items = events_method(run_id, limit=limit, offset=offset)
        return {"run_id": run_id, "items": items, "count": len(items), "limit": limit, "offset": offset}

    try:
        trace = repository.read_trace(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    items = list((trace.get("events") or [])[offset : offset + limit])
    return {"run_id": run_id, "items": items, "count": len(items), "limit": limit, "offset": offset}


@router.get("/hitl/pending")
async def pending_hitl(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    repository = _hitl_repository()
    list_method = getattr(repository, "list_pending_hitl", None)
    if not callable(list_method):
        raise HTTPException(status_code=501, detail="pending HITL listing is not supported")
    items = [item.to_dict() for item in list_method(limit=limit)]
    return {"items": items, "count": len(items), "limit": limit}
