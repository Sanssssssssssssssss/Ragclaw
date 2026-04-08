from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from src.backend.runtime.agent_manager import agent_manager

router = APIRouter()


def _registry_or_raise():
    try:
        return agent_manager.get_capability_registry()
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise HTTPException(status_code=503, detail="Capability registry is not initialized") from exc


@router.get("/capabilities/mcp")
async def list_mcp_capabilities() -> dict[str, Any]:
    registry = _registry_or_raise()
    items = [item.to_dict() for item in registry.list(capability_type="mcp_service")]
    return {"capabilities": items}


@router.get("/capabilities/mcp/{capability_id}")
async def get_mcp_capability(capability_id: str) -> dict[str, Any]:
    registry = _registry_or_raise()
    try:
        spec = registry.get(capability_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="MCP capability not found") from exc
    if spec.capability_type != "mcp_service":
        raise HTTPException(status_code=404, detail="MCP capability not found")
    return {"capability": spec.to_dict()}
