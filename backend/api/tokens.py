from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import runtime_config
from graph.agent import agent_manager
from graph.prompt_builder import build_system_prompt
from token_utils import count_message_usage, count_tokens

router = APIRouter()


class FileTokensRequest(BaseModel):
    """Returns a validated file-token request from request JSON and describes the requested file paths."""

    paths: list[str] = Field(default_factory=list)


@router.get("/tokens/session/{session_id}")
async def session_tokens(session_id: str) -> dict[str, int]:
    """Returns aggregate token counts from a session-id input and reports total prompt plus message usage."""

    session_manager = agent_manager.session_manager
    if session_manager is None or agent_manager.base_dir is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")

    record = session_manager.get_history(session_id)
    system_prompt = build_system_prompt(agent_manager.base_dir, runtime_config.get_rag_mode())
    message_text = []
    for item in record.get("messages", []):
        message_text.append(str(item.get("content", "")))
        for tool_call in item.get("tool_calls", []) or []:
            message_text.append(str(tool_call))
        for retrieval_step in item.get("retrieval_steps", []) or []:
            message_text.append(str(retrieval_step))

    system_tokens = count_tokens(system_prompt)
    message_tokens = count_tokens("\n".join(message_text))
    return {
        "system_tokens": system_tokens,
        "message_tokens": message_tokens,
        "total_tokens": system_tokens + message_tokens,
    }


@router.post("/tokens/files")
async def file_tokens(payload: FileTokensRequest) -> dict[str, Any]:
    """Returns per-file token counts from a file path list input and estimates token usage for local files."""

    if agent_manager.base_dir is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")

    files: list[dict[str, Any]] = []
    total = 0
    for relative_path in payload.paths:
        path = (agent_manager.base_dir / relative_path).resolve()
        if not path.exists() or path.is_dir():
            continue
        count = count_tokens(path.read_text(encoding="utf-8"))
        total += count
        files.append({"path": relative_path, "tokens": count})

    return {"files": files, "total_tokens": total}


@router.get("/tokens/message-usage/{session_id}")
async def session_message_usage(session_id: str) -> dict[str, Any]:
    """Returns per-message usage data from a session-id input and exposes input/output token counts for each turn."""

    session_manager = agent_manager.session_manager
    if session_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")

    record = session_manager.get_history(session_id)
    messages = []
    for index, item in enumerate(record.get("messages", [])):
        usage = item.get("usage")
        if not usage:
            usage = {
                "input_tokens": count_tokens(str(item.get("content", "")))
                if item.get("role") == "user"
                else 0,
                "output_tokens": count_message_usage(
                    str(item.get("content", "")),
                    item.get("tool_calls", []) or [],
                    item.get("retrieval_steps", []) or [],
                )
                if item.get("role") == "assistant"
                else 0,
            }
        messages.append(
            {
                "index": index,
                "role": item.get("role", ""),
                "usage": usage,
            }
        )
    return {"messages": messages}
