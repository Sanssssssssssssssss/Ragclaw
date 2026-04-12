from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from src.backend.api.adapters import LegacyChatAccumulator
from src.backend.api.chat import _build_runtime_and_resume_executor, _sse
from src.backend.decision.prompt_builder import build_system_prompt
from src.backend.orchestration.checkpointing import checkpoint_store
from src.backend.runtime.agent_manager import agent_manager
from src.backend.runtime.config import runtime_config

router = APIRouter()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CreateSessionRequest(BaseModel):
    title: str = "New Session"


class RenameSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)


class GenerateTitleRequest(BaseModel):
    message: str | None = None


class ResumeCheckpointRequest(BaseModel):
    stream: bool = True


class HitlDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(approve|reject|edit)$")
    stream: bool = True
    actor_id: str | None = Field(default=None, max_length=200)
    actor_type: str | None = Field(default=None, max_length=100)
    edited_input: dict[str, Any] | None = None


def _session_manager_or_raise():
    session_manager = agent_manager.session_manager
    if session_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")
    return session_manager


def _resolve_capability_tool(capability_id: str):
    for tool in list(getattr(agent_manager, "tools", []) or []):
        if str(getattr(tool, "name", "") or "") == str(capability_id or ""):
            return tool
    return None


def _validated_hitl_edit_payload(capability_id: str, edited_input: dict[str, Any] | None) -> dict[str, Any]:
    if edited_input is None:
        raise HTTPException(status_code=400, detail="edited_input is required for edit decisions")
    if not isinstance(edited_input, dict):
        raise HTTPException(status_code=400, detail="edited_input must be an object")
    tool = _resolve_capability_tool(capability_id)
    if tool is None:
        return dict(edited_input)
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is None or not hasattr(args_schema, "model_validate"):
        return dict(edited_input)
    try:
        model = args_schema.model_validate(edited_input)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail={"message": "edited_input failed schema validation", "errors": exc.errors()}) from exc
    if hasattr(model, "model_dump"):
        return dict(model.model_dump())
    return dict(edited_input)


@router.get("/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    return _session_manager_or_raise().list_sessions()


@router.post("/sessions")
async def create_session(payload: CreateSessionRequest) -> dict[str, Any]:
    return _session_manager_or_raise().create_session(title=payload.title)


@router.put("/sessions/{session_id}")
async def rename_session(session_id: str, payload: RenameSessionRequest) -> dict[str, Any]:
    return _session_manager_or_raise().rename_session(session_id, payload.title)


@router.post("/sessions/{session_id}/archive")
async def archive_session(session_id: str) -> dict[str, Any]:
    return _session_manager_or_raise().archive_session(session_id)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, bool]:
    _session_manager_or_raise().delete_session(session_id)
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str) -> dict[str, Any]:
    session_manager = _session_manager_or_raise()
    if agent_manager.base_dir is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")
    return {
        "system_prompt": build_system_prompt(agent_manager.base_dir, runtime_config.get_rag_mode()),
        "messages": session_manager.load_session(session_id),
    }


@router.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str) -> dict[str, Any]:
    return _session_manager_or_raise().get_history(session_id)


@router.post("/sessions/{session_id}/generate-title")
async def generate_title(session_id: str, payload: GenerateTitleRequest) -> dict[str, str]:
    session_manager = _session_manager_or_raise()
    if payload.message:
        seed = payload.message
    else:
        messages = session_manager.load_session(session_id)
        first_user = next((item["content"] for item in messages if item.get("role") == "user"), "")
        seed = first_user
    title = await agent_manager.generate_title(seed or "New Session")
    session_manager.set_title(session_id, title)
    return {"session_id": session_id, "title": title}


@router.get("/sessions/{session_id}/checkpoints")
async def list_checkpoints(session_id: str) -> dict[str, Any]:
    _session_manager_or_raise()
    thread_id = checkpoint_store.thread_id_for(session_id=session_id, run_id=session_id)
    checkpoints = [item.to_dict() for item in checkpoint_store.list_thread_checkpoints(thread_id)]
    return {"session_id": session_id, "thread_id": thread_id, "checkpoints": checkpoints}


@router.get("/sessions/{session_id}/checkpoints/{checkpoint_id}")
async def get_checkpoint(session_id: str, checkpoint_id: str) -> dict[str, Any]:
    _session_manager_or_raise()
    thread_id = checkpoint_store.thread_id_for(session_id=session_id, run_id=session_id)
    checkpoint = checkpoint_store.get_checkpoint(thread_id=thread_id, checkpoint_id=checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    return {"session_id": session_id, "thread_id": thread_id, "checkpoint": checkpoint.to_dict()}


@router.get("/sessions/{session_id}/hitl")
async def get_pending_hitl(session_id: str) -> dict[str, Any]:
    _session_manager_or_raise()
    thread_id = checkpoint_store.thread_id_for(session_id=session_id, run_id=session_id)
    pending = checkpoint_store.pending_hitl(thread_id=thread_id)
    requests = []
    for request in checkpoint_store.list_hitl_requests(thread_id=thread_id):
        decision = checkpoint_store.get_hitl_decision(request_id=request.request_id)
        requests.append(
            {
                "request": request.to_dict(),
                "decision": decision.to_dict() if decision is not None else None,
            }
        )
    return {
        "session_id": session_id,
        "thread_id": thread_id,
        "pending_interrupt": pending.to_dict() if pending is not None else None,
        "requests": requests,
    }


@router.get("/sessions/{session_id}/hitl/{checkpoint_id}")
async def get_hitl_audit(session_id: str, checkpoint_id: str) -> dict[str, Any]:
    _session_manager_or_raise()
    thread_id = checkpoint_store.thread_id_for(session_id=session_id, run_id=session_id)
    request = checkpoint_store.get_hitl_request(thread_id=thread_id, checkpoint_id=checkpoint_id)
    if request is None:
        raise HTTPException(status_code=404, detail="HITL request not found")
    decision = checkpoint_store.get_hitl_decision(thread_id=thread_id, checkpoint_id=checkpoint_id)
    return {
        "session_id": session_id,
        "thread_id": thread_id,
        "request": request.to_dict(),
        "decision": decision.to_dict() if decision is not None else None,
    }


async def _stream_checkpoint_resume(
    *,
    session_id: str,
    checkpoint_id: str,
    history: list[dict[str, Any]],
    session_manager,
    resume_message: str,
    resume_source: str,
    resume_payload: dict[str, Any] | None,
    run_status: str,
    persist_user_message: bool,
):
    runtime, executor = _build_runtime_and_resume_executor(
        checkpoint_id=checkpoint_id,
        thread_id=checkpoint_store.thread_id_for(session_id=session_id, run_id=session_id),
        resume_source=resume_source,
        resume_payload=resume_payload,
    )
    accumulator = LegacyChatAccumulator()
    persisted = False

    def persist(error_message: str | None = None) -> None:
        nonlocal persisted
        if persisted:
            return
        accumulator.persist(
            session_manager=session_manager,
            session_id=session_id,
            user_message=resume_message,
            error_message=error_message,
            persist_user_message=persist_user_message,
        )
        persisted = True

    try:
        async for harness_event in runtime.run_with_executor(
            user_message=resume_message,
            session_id=session_id,
            source=resume_source,
            executor=executor,
            history=history,
            suppress_failures=True,
            thread_id=checkpoint_store.thread_id_for(session_id=session_id, run_id=session_id),
            checkpoint_id=checkpoint_id,
            resume_source=resume_source,
            run_status=run_status,
            orchestration_engine="langgraph",
        ):
            for event_type, data in accumulator.consume(harness_event):
                yield _sse(event_type, data)
                if event_type == "done":
                    persist()
                elif event_type == "error":
                    persist(error_message=str(data.get("error", "") or "unknown error"))
    except Exception as exc:  # pragma: no cover - defensive boundary
        persist(error_message=str(exc) or "unknown error")
        yield _sse("error", {"error": str(exc)})

    if not persisted:
        persist()


@router.post("/sessions/{session_id}/checkpoints/{checkpoint_id}/resume")
async def resume_from_checkpoint(
    session_id: str,
    checkpoint_id: str,
    payload: ResumeCheckpointRequest,
):
    session_manager = _session_manager_or_raise()
    thread_id = checkpoint_store.thread_id_for(session_id=session_id, run_id=session_id)
    checkpoint = checkpoint_store.get_checkpoint(thread_id=thread_id, checkpoint_id=checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    if not checkpoint.resume_eligible:
        raise HTTPException(status_code=400, detail="Checkpoint is not eligible for resume")

    history = session_manager.load_session_for_agent(session_id)
    resume_message = checkpoint.user_message or "Resume from checkpoint"

    async def event_generator():
        async for item in _stream_checkpoint_resume(
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            history=history,
            session_manager=session_manager,
            resume_message=resume_message,
            resume_source="checkpoint_api",
            resume_payload=None,
            run_status="resumed",
            persist_user_message=False,
        ):
            yield item

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
    return JSONResponse(
        {
            "content": final_content,
            "run_status": "resumed",
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
            "resume_source": "checkpoint_api",
        }
    )


@router.post("/sessions/{session_id}/hitl/{checkpoint_id}/decision")
async def submit_hitl_decision(
    session_id: str,
    checkpoint_id: str,
    payload: HitlDecisionRequest,
):
    session_manager = _session_manager_or_raise()
    thread_id = checkpoint_store.thread_id_for(session_id=session_id, run_id=session_id)
    actor_id = str(payload.actor_id or "").strip() or f"session:{session_id}"
    actor_type = str(payload.actor_type or "").strip() or "session_user"
    request = checkpoint_store.get_hitl_request(thread_id=thread_id, checkpoint_id=checkpoint_id) if payload.decision == "edit" else None
    if payload.decision == "edit" and request is None:
        raise HTTPException(status_code=404, detail="Pending HITL interrupt not found")
    edited_input = _validated_hitl_edit_payload(request.capability_id, payload.edited_input) if request is not None else None
    request, decision_record, created = checkpoint_store.record_hitl_decision(
        thread_id=thread_id,
        checkpoint_id=checkpoint_id,
        decision=payload.decision,
        actor_id=actor_id,
        actor_type=actor_type,
        decided_at=_utc_now_iso(),
        resume_source="hitl_api",
        edited_input_snapshot=edited_input,
    )
    if request is None or decision_record is None:
        raise HTTPException(status_code=404, detail="Pending HITL interrupt not found")
    if not created:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "HITL decision already recorded",
                "request": request.to_dict(),
                "decision": decision_record.to_dict(),
            },
        )

    history = session_manager.load_session_for_agent(session_id)
    resume_message = request.reason or request.display_name or "Resume from HITL decision"

    async def event_generator():
        async for item in _stream_checkpoint_resume(
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            history=history,
            session_manager=session_manager,
            resume_message=resume_message,
            resume_source="hitl_api",
            resume_payload={
                "decision": payload.decision,
                "request_id": request.request_id,
                "decision_id": decision_record.decision_id,
                "actor_id": decision_record.actor_id,
                "actor_type": decision_record.actor_type,
                "decided_at": decision_record.decided_at,
                "resume_source": decision_record.resume_source,
                "edited_input": edited_input,
            },
            run_status="restoring",
            persist_user_message=False,
        ):
            yield item

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
    return JSONResponse(
        {
            "content": final_content,
            "run_status": "resumed",
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
            "resume_source": "hitl_api",
            "decision": payload.decision,
            "request_id": request.request_id,
            "decision_id": decision_record.decision_id,
        }
    )
