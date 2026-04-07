from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver


@dataclass(frozen=True)
class CheckpointSummary:
    checkpoint_id: str
    thread_id: str
    checkpoint_ns: str
    created_at: str
    source: str
    step: int
    run_id: str
    session_id: str | None
    user_message: str
    route_intent: str
    final_answer: str
    is_latest: bool
    state_label: str
    resume_eligible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "thread_id": self.thread_id,
            "checkpoint_ns": self.checkpoint_ns,
            "created_at": self.created_at,
            "source": self.source,
            "step": self.step,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "user_message": self.user_message,
            "route_intent": self.route_intent,
            "final_answer": self.final_answer,
            "is_latest": self.is_latest,
            "state_label": self.state_label,
            "resume_eligible": self.resume_eligible,
        }


@dataclass(frozen=True)
class PendingHitlRequest:
    run_id: str
    thread_id: str
    session_id: str | None
    capability_id: str
    capability_type: str
    display_name: str
    risk_level: str
    reason: str
    proposed_input: dict[str, Any]
    checkpoint_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "session_id": self.session_id,
            "capability_id": self.capability_id,
            "capability_type": self.capability_type,
            "display_name": self.display_name,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "proposed_input": dict(self.proposed_input),
            "checkpoint_id": self.checkpoint_id,
        }


class LangGraphCheckpointStore:
    def __init__(self) -> None:
        self._saver = InMemorySaver()
        self._pending_hitl: dict[str, PendingHitlRequest] = {}

    @property
    def saver(self) -> InMemorySaver:
        return self._saver

    def thread_id_for(self, *, session_id: str | None, run_id: str) -> str:
        return str(session_id or run_id)

    def list_thread_checkpoints(self, thread_id: str, *, limit: int | None = 50) -> list[CheckpointSummary]:
        config = {"configurable": {"thread_id": thread_id}}
        tuples = list(self._saver.list(config, limit=limit))
        latest_id = ""
        if tuples:
            latest_id = str(tuples[0].config.get("configurable", {}).get("checkpoint_id", "") or "")
        return [self._tuple_to_summary(item, latest_id=latest_id) for item in tuples]

    def get_checkpoint(self, *, thread_id: str, checkpoint_id: str) -> CheckpointSummary | None:
        for item in self.list_thread_checkpoints(thread_id):
            if item.checkpoint_id == checkpoint_id:
                return item
        return None

    def checkpoint_config(self, *, thread_id: str, checkpoint_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}}

    def latest_checkpoint(self, *, thread_id: str) -> CheckpointSummary | None:
        items = self.list_thread_checkpoints(thread_id, limit=1)
        return items[0] if items else None

    def record_pending_hitl(self, request: PendingHitlRequest) -> None:
        self._pending_hitl[request.thread_id] = request

    def clear_pending_hitl(self, *, thread_id: str, checkpoint_id: str | None = None) -> None:
        pending = self._pending_hitl.get(thread_id)
        if pending is None:
            return
        if checkpoint_id and pending.checkpoint_id != checkpoint_id:
            return
        self._pending_hitl.pop(thread_id, None)

    def pending_hitl(self, *, thread_id: str) -> PendingHitlRequest | None:
        return self._pending_hitl.get(thread_id)

    def _tuple_to_summary(self, item, *, latest_id: str) -> CheckpointSummary:
        config = dict(getattr(item, "config", {}) or {})
        configurable = dict(config.get("configurable", {}) or {})
        checkpoint = dict(getattr(item, "checkpoint", {}) or {})
        metadata = dict(getattr(item, "metadata", {}) or {})
        channel_values = dict(checkpoint.get("channel_values", {}) or {})

        checkpoint_id = str(configurable.get("checkpoint_id", "") or checkpoint.get("id", "") or "")
        is_latest = checkpoint_id == latest_id
        source = str(metadata.get("source", "") or "")
        step = int(metadata.get("step", -1) or -1)
        final_answer = str(channel_values.get("final_answer", "") or "")
        pending = self._pending_hitl.get(str(configurable.get("thread_id", "") or ""))
        has_pending_hitl = pending is not None and pending.checkpoint_id == checkpoint_id
        state_label = "interrupted" if has_pending_hitl else ("fresh" if is_latest else "interrupted")
        resume_eligible = (has_pending_hitl or (not is_latest and step >= 0 and not final_answer.strip()))
        return CheckpointSummary(
            checkpoint_id=checkpoint_id,
            thread_id=str(configurable.get("thread_id", "") or ""),
            checkpoint_ns=str(configurable.get("checkpoint_ns", "") or ""),
            created_at=str(checkpoint.get("ts", "") or ""),
            source=source,
            step=step,
            run_id=str(channel_values.get("run_id", "") or ""),
            session_id=str(channel_values.get("session_id")) if channel_values.get("session_id") is not None else None,
            user_message=str(channel_values.get("user_message", "") or ""),
            route_intent=str(getattr(channel_values.get("route_decision"), "intent", "") or ""),
            final_answer=final_answer,
            is_latest=is_latest,
            state_label=state_label,
            resume_eligible=resume_eligible,
        )


checkpoint_store = LangGraphCheckpointStore()
