from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from graph.agent import agent_manager
from harness.adapters import ChatTraceShadowState, build_chat_runtime, consume_legacy_chat_event

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str
    stream: bool = True


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _new_segment() -> dict[str, Any]:
    return {"content": "", "tool_calls": [], "retrieval_steps": [], "usage": None}


_AUTO_TITLE_PLACEHOLDERS = {
    "",
    "new session",
    "新会话",
    "新对话",
}


def _should_auto_generate_title(history_record: dict[str, Any], is_first_user_message: bool) -> bool:
    if not is_first_user_message:
        return False

    current_title = str(history_record.get("title", "") or "").strip().lower()
    return current_title in _AUTO_TITLE_PLACEHOLDERS


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
        segments: list[dict[str, Any]] = []
        current_segment = _new_segment()
        conversation_saved = False
        runtime = None
        run_handle = None
        shadow_state = None
        run_finalized = False

        if getattr(agent_manager, "base_dir", None) is not None:
            runtime = build_chat_runtime(agent_manager.base_dir)
            run_handle = runtime.begin_run(
                user_message=payload.message,
                session_id=payload.session_id,
                source="chat_api",
            )
            shadow_state = ChatTraceShadowState()

        def persist_segments(fallback_content: str | None = None) -> None:
            nonlocal current_segment, conversation_saved
            if conversation_saved:
                return

            if fallback_content:
                if current_segment["content"].strip():
                    current_segment["content"] = (
                        f"{current_segment['content'].rstrip()}\n\n{fallback_content}"
                    )
                else:
                    current_segment["content"] = fallback_content

            if (
                current_segment["content"].strip()
                or current_segment["tool_calls"]
                or current_segment["retrieval_steps"]
            ):
                segments.append(current_segment)
                current_segment = _new_segment()

            session_manager.save_message(payload.session_id, "user", payload.message)
            for segment in segments:
                session_manager.save_message(
                    payload.session_id,
                    "assistant",
                    segment["content"],
                    tool_calls=segment["tool_calls"] or None,
                    retrieval_steps=segment["retrieval_steps"] or None,
                    usage=segment.get("usage") or None,
                )

            conversation_saved = True

        try:
            async for event in agent_manager.astream(payload.message, history):
                event_type = event["type"]
                if runtime is not None and run_handle is not None and shadow_state is not None:
                    should_forward = consume_legacy_chat_event(runtime, run_handle.run_id, event, shadow_state)
                    if not should_forward:
                        continue

                if event_type == "token":
                    current_segment["content"] += event.get("content", "")
                elif event_type == "tool_start":
                    current_segment["tool_calls"].append(
                        {
                            "tool": event.get("tool", "tool"),
                            "input": event.get("input", ""),
                            "output": "",
                        }
                    )
                elif event_type == "tool_end":
                    if current_segment["tool_calls"]:
                        current_segment["tool_calls"][-1]["output"] = event.get("output", "")
                elif event_type == "retrieval":
                    current_segment["retrieval_steps"].append(
                        {
                            "kind": event.get("kind", "knowledge"),
                            "stage": event.get("stage", "unknown"),
                            "title": event.get("title", "检索结果"),
                            "message": event.get("message", ""),
                            "results": event.get("results", []),
                        }
                    )
                elif event_type == "new_response":
                    if (
                        current_segment["content"].strip()
                        or current_segment["tool_calls"]
                        or current_segment["retrieval_steps"]
                    ):
                        segments.append(current_segment)
                    current_segment = _new_segment()
                elif event_type == "done":
                    if not current_segment["content"].strip() and event.get("content"):
                        current_segment["content"] = event["content"]
                    if event.get("usage"):
                        current_segment["usage"] = event["usage"]
                    persist_segments()
                    if not run_finalized and runtime is not None and run_handle is not None and shadow_state is not None:
                        runtime.complete_run(
                            run_handle.run_id,
                            final_answer=shadow_state.final_answer,
                            route_intent=shadow_state.route_intent,
                            used_skill=shadow_state.used_skill,
                            tool_names=tuple(shadow_state.tool_names),
                            retrieval_sources=tuple(shadow_state.retrieval_sources),
                        )
                        run_finalized = True

                data = {key: value for key, value in event.items() if key != "type"}
                yield _sse(event_type, data)

                if event_type == "done" and _should_auto_generate_title(history_record, is_first_user_message):
                    title = await agent_manager.generate_title(payload.message)
                    session_manager.set_title(payload.session_id, title)
                    yield _sse(
                        "title",
                        {"session_id": payload.session_id, "title": title},
                    )
        except Exception as exc:
            persist_segments(fallback_content=f"请求失败: {str(exc) or 'unknown error'}")
            if not run_finalized and runtime is not None and run_handle is not None and shadow_state is not None:
                runtime.fail_run(
                    run_handle.run_id,
                    error_message=str(exc) or "unknown error",
                    route_intent=shadow_state.route_intent,
                    used_skill=shadow_state.used_skill,
                    tool_names=tuple(shadow_state.tool_names),
                    retrieval_sources=tuple(shadow_state.retrieval_sources),
                )
                run_finalized = True
            yield _sse("error", {"error": str(exc)})

    if payload.stream:
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    final_text = ""
    async for raw_event in event_generator():
        if raw_event.startswith("event: done"):
            final_text = raw_event
    return JSONResponse({"content": final_text})
