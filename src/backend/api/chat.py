from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.backend.api.adapters import LegacyChatAccumulator
from src.backend.runtime.agent_manager import agent_manager

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str
    stream: bool = True


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_AUTO_TITLE_PLACEHOLDERS = {
    "",
    "new session",
    "鏂颁細璇?",
    "鏂板璇?",
}


def _should_auto_generate_title(history_record: dict[str, Any], is_first_user_message: bool) -> bool:
    if not is_first_user_message:
        return False
    current_title = str(history_record.get("title", "") or "").strip().lower()
    return current_title in _AUTO_TITLE_PLACEHOLDERS


def _build_runtime_and_executor():
    runtime = agent_manager.get_harness_runtime()
    executor = agent_manager.create_harness_executor()
    return runtime, executor


def _build_runtime_and_resume_executor(
    *,
    checkpoint_id: str,
    thread_id: str,
    resume_source: str,
):
    runtime = agent_manager.get_harness_runtime()
    executor = agent_manager.create_harness_executor(
        resume_checkpoint_id=checkpoint_id,
        resume_thread_id=thread_id,
        resume_source=resume_source,
    )
    return runtime, executor


@router.post("/chat")
async def chat(payload: ChatRequest):
    session_manager = agent_manager.session_manager
    if session_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")

    history_record = session_manager.load_session_record(payload.session_id)
    history = session_manager.load_session_for_agent(payload.session_id)
    is_first_user_message = not any(
        message.get("role") == "user"
        for message in history_record.get("messages", [])
    )

    async def event_generator():
        runtime, executor = _build_runtime_and_executor()
        accumulator = LegacyChatAccumulator()
        persisted = False

        def persist(error_message: str | None = None) -> None:
            nonlocal persisted
            if persisted:
                return
            accumulator.persist(
                session_manager=session_manager,
                session_id=payload.session_id,
                user_message=payload.message,
                error_message=error_message,
            )
            persisted = True

        try:
            async for harness_event in runtime.run_with_executor(
                user_message=payload.message,
                session_id=payload.session_id,
                source="chat_api",
                executor=executor,
                history=history,
                suppress_failures=True,
                thread_id=payload.session_id,
                run_status="fresh",
                orchestration_engine="langgraph",
            ):
                for event_type, data in accumulator.consume(harness_event):
                    yield _sse(event_type, data)
                    if event_type == "done":
                        if _should_auto_generate_title(history_record, is_first_user_message):
                            title = await agent_manager.generate_title(payload.message)
                            session_manager.set_title(payload.session_id, title)
                            yield _sse("title", {"session_id": payload.session_id, "title": title})
                        persist()
                    elif event_type == "error":
                        persist(error_message=str(data.get("error", "") or "unknown error"))
        except Exception as exc:  # pragma: no cover - defensive boundary
            persist(error_message=str(exc) or "unknown error")
            yield _sse("error", {"error": str(exc)})

        if not persisted:
            persist()

    if payload.stream:
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    final_content = ""
    async for raw_event in event_generator():
        if raw_event.startswith("event: done"):
            for line in raw_event.splitlines():
                if line.startswith("data: "):
                    data = json.loads(line[len("data: ") :])
                    final_content = str(data.get("content", "") or "")
                    break
    return JSONResponse({"content": final_content})
