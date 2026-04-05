"""Compatibility adapters between canonical harness events and legacy chat SSE/session semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.backend.observability.types import HarnessEvent


def _new_segment() -> dict[str, Any]:
    return {"content": "", "tool_calls": [], "retrieval_steps": [], "usage": None}


@dataclass
class LegacyChatAccumulator:
    """Accumulate canonical harness events into legacy SSE and persisted assistant segments."""

    current_segment_index: int = 0
    current_segment: dict[str, Any] = field(default_factory=_new_segment)
    segments: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""
    last_done_payload: dict[str, Any] = field(default_factory=dict)

    def _commit_current_segment(self) -> None:
        if (
            self.current_segment["content"].strip()
            or self.current_segment["tool_calls"]
            or self.current_segment["retrieval_steps"]
        ):
            self.segments.append(self.current_segment)
        self.current_segment = _new_segment()

    def _ensure_segment(self, segment_index: int) -> list[tuple[str, dict[str, Any]]]:
        legacy_events: list[tuple[str, dict[str, Any]]] = []
        if segment_index > self.current_segment_index:
            self._commit_current_segment()
            self.current_segment_index = segment_index
            legacy_events.append(("new_response", {}))
        return legacy_events

    def consume(self, event: HarnessEvent) -> list[tuple[str, dict[str, Any]]]:
        payload = dict(event.payload)
        legacy_events: list[tuple[str, dict[str, Any]]] = []

        if event.name in {"run.queued", "run.dequeued"}:
            legacy_events.append((event.name, payload))
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
            self.last_done_payload = {"content": self.current_segment["content"], "usage": usage or None}
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
    ) -> None:
        if error_message:
            suffix = f"请求失败: {error_message}"
            if self.current_segment["content"].strip():
                self.current_segment["content"] = f"{self.current_segment['content'].rstrip()}\n\n{suffix}"
            else:
                self.current_segment["content"] = suffix

        self._commit_current_segment()
        session_manager.save_message(session_id, "user", user_message)
        for segment in self.segments:
            session_manager.save_message(
                session_id,
                "assistant",
                segment["content"],
                tool_calls=segment["tool_calls"] or None,
                retrieval_steps=segment["retrieval_steps"] or None,
                usage=segment.get("usage") or None,
            )
