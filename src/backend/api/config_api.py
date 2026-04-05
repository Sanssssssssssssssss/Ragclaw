from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Literal

from src.backend.runtime.config import runtime_config

router = APIRouter()


class RagModeRequest(BaseModel):
    enabled: bool


class ExecutionPlatformRequest(BaseModel):
    platform: Literal["windows", "linux"]


class SkillRetrievalRequest(BaseModel):
    enabled: bool


@router.get("/config/rag-mode")
async def get_rag_mode() -> dict[str, bool]:
    return {"enabled": runtime_config.get_rag_mode()}


@router.put("/config/rag-mode")
async def set_rag_mode(payload: RagModeRequest) -> dict[str, bool]:
    config = runtime_config.set_rag_mode(payload.enabled)
    return {"enabled": bool(config["rag_mode"])}


@router.get("/config/execution-platform")
async def get_execution_platform() -> dict[str, str]:
    return {"platform": runtime_config.get_execution_platform()}


@router.put("/config/execution-platform")
async def set_execution_platform(payload: ExecutionPlatformRequest) -> dict[str, str]:
    config = runtime_config.set_execution_platform(payload.platform)
    return {"platform": str(config["execution_platform"])}


@router.get("/config/skill-retrieval")
async def get_skill_retrieval() -> dict[str, bool]:
    return {"enabled": runtime_config.get_skill_retrieval_enabled()}


@router.put("/config/skill-retrieval")
async def set_skill_retrieval(payload: SkillRetrievalRequest) -> dict[str, bool]:
    config = runtime_config.set_skill_retrieval_enabled(payload.enabled)
    return {"enabled": bool(config["skill_retrieval_enabled"])}
