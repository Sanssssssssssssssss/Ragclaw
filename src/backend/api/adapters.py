"""Compatibility adapters between canonical harness events and legacy chat SSE/session semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.backend.observability.types import HarnessEvent


def _new_segment(
    *,
    run_meta: dict[str, Any] | None = None,
    checkpoint_events: list[dict[str, Any]] | None = None,
    hitl_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "content": "",
        "tool_calls": [],
        "retrieval_steps": [],
        "usage": None,
        "run_meta": dict(run_meta or {}),
        "checkpoint_events": [dict(item) for item in (checkpoint_events or [])],
        "hitl_events": [dict(item) for item in (hitl_events or [])],
    }


@dataclass
class LegacyChatAccumulator:
    """Accumulate canonical harness events into legacy SSE and persisted assistant segments."""

    current_segment_index: int = 0
    current_segment: dict[str, Any] = field(default_factory=_new_segment)
    segments: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""
    last_done_payload: dict[str, Any] = field(default_factory=dict)
    run_meta: dict[str, Any] = field(default_factory=dict)
    checkpoint_events: list[dict[str, Any]] = field(default_factory=list)
    hitl_events: list[dict[str, Any]] = field(default_factory=list)

    def _commit_current_segment(self) -> None:
        if (
            self.current_segment["content"].strip()
            or self.current_segment["tool_calls"]
            or self.current_segment["retrieval_steps"]
            or self.current_segment["checkpoint_events"]
            or self.current_segment["hitl_events"]
        ):
            self.segments.append(self.current_segment)
        self.current_segment = _new_segment(
            run_meta=self.run_meta,
            checkpoint_events=self.checkpoint_events,
            hitl_events=self.hitl_events,
        )

    def _ensure_segment(self, segment_index: int) -> list[tuple[str, dict[str, Any]]]:
        legacy_events: list[tuple[str, dict[str, Any]]] = []
        if segment_index > self.current_segment_index:
            self._commit_current_segment()
            self.current_segment_index = segment_index
            legacy_events.append(("new_response", {}))
        return legacy_events

    def _set_run_meta(self, payload: dict[str, Any]) -> dict[str, Any]:
        next_meta = {
            "status": str(payload.get("run_status", "") or self.run_meta.get("status") or "fresh"),
            "thread_id": str(payload.get("thread_id", "") or self.run_meta.get("thread_id", "") or ""),
            "checkpoint_id": str(payload.get("checkpoint_id", "") or self.run_meta.get("checkpoint_id", "") or ""),
            "resume_source": str(payload.get("resume_source", "") or self.run_meta.get("resume_source", "") or ""),
            "orchestration_engine": str(
                payload.get("orchestration_engine", "") or self.run_meta.get("orchestration_engine", "") or ""
            ),
        }
        self.run_meta = next_meta
        self.current_segment["run_meta"] = dict(next_meta)
        return next_meta

    def _append_checkpoint_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        checkpoint_event = {
            "type": event_type,
            "checkpoint_id": str(payload.get("checkpoint_id", "") or ""),
            "thread_id": str(payload.get("thread_id", "") or self.run_meta.get("thread_id", "") or ""),
            "resume_source": str(payload.get("resume_source", "") or self.run_meta.get("resume_source", "") or ""),
            "state_label": str(payload.get("state_label", "") or ""),
            "created_at": str(payload.get("created_at", "") or ""),
            "orchestration_engine": str(
                payload.get("orchestration_engine", "") or self.run_meta.get("orchestration_engine", "") or ""
            ),
        }
        self.checkpoint_events.append(checkpoint_event)
        self.current_segment["checkpoint_events"] = [dict(item) for item in self.checkpoint_events]
        return checkpoint_event

    def _append_hitl_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        hitl_event = {
            "type": event_type,
            "request_id": str(payload.get("request_id", "") or ""),
            "requested_at": str(payload.get("requested_at", "") or ""),
            "decision_id": str(payload.get("decision_id", "") or ""),
            "decision": str(payload.get("decision", "") or ""),
            "actor_id": str(payload.get("actor_id", "") or ""),
            "actor_type": str(payload.get("actor_type", "") or ""),
            "decided_at": str(payload.get("decided_at", "") or ""),
            "run_id": str(payload.get("run_id", "") or ""),
            "thread_id": str(payload.get("thread_id", "") or self.run_meta.get("thread_id", "") or ""),
            "session_id": str(payload.get("session_id", "") or ""),
            "capability_id": str(payload.get("capability_id", "") or ""),
            "capability_type": str(payload.get("capability_type", "") or ""),
            "display_name": str(payload.get("display_name", "") or ""),
            "risk_level": str(payload.get("risk_level", "") or ""),
            "reason": str(payload.get("reason", "") or ""),
            "proposed_input": dict(payload.get("proposed_input", {}) or {}),
            "approved_input_snapshot": dict(payload.get("approved_input_snapshot", {}) or {}) if payload.get("approved_input_snapshot") is not None else None,
            "edited_input_snapshot": dict(payload.get("edited_input_snapshot", {}) or {}) if payload.get("edited_input_snapshot") is not None else None,
            "rejected_input_snapshot": dict(payload.get("rejected_input_snapshot", {}) or {}) if payload.get("rejected_input_snapshot") is not None else None,
            "checkpoint_id": str(payload.get("checkpoint_id", "") or self.run_meta.get("checkpoint_id", "") or ""),
            "resume_source": str(payload.get("resume_source", "") or self.run_meta.get("resume_source", "") or ""),
            "orchestration_engine": str(
                payload.get("orchestration_engine", "") or self.run_meta.get("orchestration_engine", "") or ""
            ),
        }
        self.hitl_events.append(hitl_event)
        self.current_segment["hitl_events"] = [dict(item) for item in self.hitl_events]
        return hitl_event

    def consume(self, event: HarnessEvent) -> list[tuple[str, dict[str, Any]]]:
        payload = dict(event.payload)
        legacy_events: list[tuple[str, dict[str, Any]]] = []

        if event.name == "run.started":
            legacy_events.append(("run_status", self._set_run_meta(payload)))
            return legacy_events

        if event.name in {"run.queued", "run.dequeued"}:
            legacy_events.append((event.name, payload))
            return legacy_events

        if event.name == "checkpoint.created":
            checkpoint_event = self._append_checkpoint_event("created", payload)
            self._set_run_meta(payload)
            legacy_events.append(("checkpoint_created", checkpoint_event))
            return legacy_events

        if event.name == "checkpoint.resumed":
            checkpoint_event = self._append_checkpoint_event("resumed", payload)
            payload = dict(payload)
            payload["run_status"] = "resumed"
            legacy_events.append(("run_status", self._set_run_meta(payload)))
            legacy_events.append(("checkpoint_resumed", checkpoint_event))
            return legacy_events

        if event.name == "checkpoint.interrupted":
            checkpoint_event = self._append_checkpoint_event("interrupted", payload)
            payload = dict(payload)
            payload["run_status"] = "interrupted"
            legacy_events.append(("run_status", self._set_run_meta(payload)))
            legacy_events.append(("checkpoint_interrupted", checkpoint_event))
            return legacy_events

        if event.name == "hitl.requested":
            hitl_event = self._append_hitl_event("requested", payload)
            payload = dict(payload)
            payload["run_status"] = "interrupted"
            legacy_events.append(("run_status", self._set_run_meta(payload)))
            legacy_events.append(("hitl_requested", hitl_event))
            return legacy_events

        if event.name == "hitl.approved":
            hitl_event = self._append_hitl_event("approved", payload)
            legacy_events.append(("hitl_approved", hitl_event))
            return legacy_events

        if event.name == "hitl.rejected":
            hitl_event = self._append_hitl_event("rejected", payload)
            legacy_events.append(("hitl_rejected", hitl_event))
            return legacy_events

        if event.name == "hitl.edited":
            hitl_event = self._append_hitl_event("edited", payload)
            legacy_events.append(("hitl_edited", hitl_event))
            return legacy_events

        if event.name == "retrieval.completed":
            retrieval_step = {
                "kind": payload.get("kind", "knowledge"),
                "stage": payload.get("stage", "unknown"),
                "title": payload.get("title", "retrieval"),
                "message": payload.get("message", ""),
                "results": payload.get("results", []),
                "status": payload.get("status", ""),
                "reason": payload.get("reason", ""),
            }
            self.current_segment["retrieval_steps"].append(retrieval_step)
            legacy_events.append(("retrieval", retrieval_step))
            return legacy_events

        if event.name == "tool.started":
            tool_call = {
                "tool": payload.get("tool", "tool"),
                "input": payload.get("input", ""),
                "output": "",
                "call_id": payload.get("call_id", ""),
            }
            self.current_segment["tool_calls"].append(tool_call)
            legacy_events.append(
                (
                    "tool_start",
                    {
                        "tool": tool_call["tool"],
                        "input": tool_call["input"],
                        "call_id": tool_call["call_id"],
                    },
                )
            )
            return legacy_events

        if event.name == "tool.completed":
            call_id = str(payload.get("call_id", "") or "")
            tool_name = str(payload.get("tool", "tool") or "tool")
            output = str(payload.get("output", "") or "")
            for item in reversed(self.current_segment["tool_calls"]):
                if call_id and str(item.get("call_id", "") or "") == call_id:
                    item["output"] = output
                    break
                if not call_id and str(item.get("tool", "") or "") == tool_name and not str(item.get("output", "") or ""):
                    item["output"] = output
                    break
            legacy_events.append(
                (
                    "tool_end",
                    {
                        "tool": tool_name,
                        "input": payload.get("input", ""),
                        "output": output,
                        "call_id": call_id,
                    },
                )
            )
            return legacy_events

        if event.name == "answer.started":
            segment_index = int(payload.get("segment_index", 0) or 0)
            legacy_events.extend(self._ensure_segment(segment_index))
            return legacy_events

        if event.name == "answer.delta":
            segment_index = int(payload.get("segment_index", 0) or 0)
            legacy_events.extend(self._ensure_segment(segment_index))
            content = str(payload.get("content", "") or "")
            if content:
                self.current_segment["content"] += content
                self.final_answer += content
                legacy_events.append(("token", {"content": content}))
            return legacy_events

        if event.name == "answer.completed":
            segment_index = int(payload.get("segment_index", 0) or 0)
            legacy_events.extend(self._ensure_segment(segment_index))
            content = str(payload.get("content", "") or "").strip()
            if content:
                self.current_segment["content"] = content
                self.final_answer = content
            usage: dict[str, Any] = {}
            if payload.get("input_tokens") is not None:
                usage["input_tokens"] = int(payload["input_tokens"])
            if payload.get("output_tokens") is not None:
                usage["output_tokens"] = int(payload["output_tokens"])
            if usage:
                self.current_segment["usage"] = usage
            self.last_done_payload = {
                "content": self.current_segment["content"],
                "usage": usage or None,
                "run_meta": dict(self.current_segment.get("run_meta") or {}),
                "checkpoint_events": [dict(item) for item in self.current_segment.get("checkpoint_events", [])],
                "hitl_events": [dict(item) for item in self.current_segment.get("hitl_events", [])],
            }
            legacy_events.append(("done", dict(self.last_done_payload)))
            return legacy_events

        if event.name == "run.failed":
            legacy_events.append(("error", {"error": str(payload.get("error_message", "") or "unknown error")}))
            return legacy_events

        return legacy_events

    def persist(
        self,
        *,
        session_manager,
        session_id: str,
        user_message: str,
        error_message: str | None = None,
        persist_user_message: bool = True,
    ) -> None:
        if error_message:
            suffix = f"璇锋眰澶辫触: {error_message}"
            if self.current_segment["content"].strip():
                self.current_segment["content"] = f"{self.current_segment['content'].rstrip()}\n\n{suffix}"
            else:
                self.current_segment["content"] = suffix

        self._commit_current_segment()
        if persist_user_message:
            session_manager.save_message(session_id, "user", user_message)
        for segment in self.segments:
            session_manager.save_message(
                session_id,
                "assistant",
                segment["content"],
                tool_calls=segment["tool_calls"] or None,
                retrieval_steps=segment["retrieval_steps"] or None,
                usage=segment.get("usage") or None,
                run_meta=segment.get("run_meta") or None,
                checkpoint_events=segment.get("checkpoint_events") or None,
                hitl_events=segment.get("hitl_events") or None,
            )
