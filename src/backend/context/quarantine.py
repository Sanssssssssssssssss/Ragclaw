from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.backend.context.episodic_memory import build_episodic_summary
from src.backend.context.manifest import render_memory_index
from src.backend.context.models import ContextTurnSnapshot, EpisodicSummary, StoredMemory, WorkingMemory
from src.backend.context.store import context_store
from src.backend.context.working_memory import build_working_memory


_TAG_PATTERN = re.compile(r"[A-Za-z0-9_./:-]{3,}")


def _tags_for(text: str) -> tuple[str, ...]:
    seen: list[str] = []
    for match in _TAG_PATTERN.finditer(str(text or "")):
        token = match.group(0).lower()
        if token not in seen:
            seen.append(token)
        if len(seen) >= 8:
            break
    return tuple(seen)


@dataclass(frozen=True)
class ContextQuarantineResult:
    action: str
    session_id: str
    turn_id: str
    run_id: str
    thread_id: str
    changed: bool
    force: bool = False
    turn: dict[str, Any] | None = None
    invalidated_memory_ids: tuple[str, ...] = ()
    deleted_memory_ids: tuple[str, ...] = ()
    invalidated_conversation_ids: tuple[str, ...] = ()
    deleted_conversation_count: int = 0
    rebuilt_snapshot: dict[str, Any] = field(default_factory=dict)
    audit_event_ids: tuple[str, ...] = ()
    blocked_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "changed": self.changed,
            "force": self.force,
            "turn": dict(self.turn or {}),
            "invalidated_memory_ids": list(self.invalidated_memory_ids),
            "deleted_memory_ids": list(self.deleted_memory_ids),
            "invalidated_conversation_ids": list(self.invalidated_conversation_ids),
            "deleted_conversation_count": self.deleted_conversation_count,
            "rebuilt_snapshot": dict(self.rebuilt_snapshot),
            "audit_event_ids": list(self.audit_event_ids),
            "blocked_reason": self.blocked_reason,
        }


class ContextQuarantineService:
    def __init__(self, *, session_manager, base_dir: Path | None = None, now_factory=None) -> None:
        self._session_manager = session_manager
        self._base_dir = Path(base_dir) if base_dir is not None else None
        self._now_factory = now_factory or (lambda: "")

    def derived_memories(self, *, session_id: str, turn_id: str) -> dict[str, Any]:
        turn = self._turn_or_raise(session_id=session_id, turn_id=turn_id)
        memories = context_store.list_memories_by_provenance(
            turn_id=turn.turn_id,
            run_id=turn.run_id,
            include_inactive=True,
        )
        conversation_chunks = context_store.list_conversation_chunks_by_provenance(
            turn_id=turn.turn_id,
            run_id=turn.run_id,
            thread_id=turn.thread_id,
            limit=200,
        )
        return {
            "session_id": session_id,
            "turn_id": turn.turn_id,
            "run_id": turn.run_id,
            "thread_id": turn.thread_id,
            "memories": [item.to_dict() for item in memories],
            "conversation_recall": [item.to_dict() for item in conversation_chunks],
            "audit_events": [
                item.to_dict()
                for item in context_store.list_context_events(thread_id=turn.thread_id, limit=80)
                if item.turn_id == turn.turn_id
            ],
        }

    def exclude_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        reason: str = "Exclude from future context",
        actor_id: str = "session_user",
    ) -> ContextQuarantineResult:
        turn = self._turn_or_raise(session_id=session_id, turn_id=turn_id)
        now = self._now()
        record_before = self._session_manager.load_session_record(session_id)
        already_excluded = bool(turn.excluded_from_context) or turn.turn_id in {
            str(item) for item in record_before.get("excluded_turn_ids", []) or []
        }
        existing_memories = context_store.list_memories_by_provenance(
            turn_id=turn.turn_id,
            run_id=turn.run_id,
            include_inactive=True,
        )
        existing_conversation_chunks = context_store.list_conversation_chunks_by_provenance(
            turn_id=turn.turn_id,
            run_id=turn.run_id,
            thread_id=turn.thread_id,
            limit=200,
        )

        audit_ids: list[str] = []
        if not already_excluded:
            self._session_manager.exclude_turn_from_context(
                session_id=session_id,
                turn_id=turn.turn_id,
                run_id=turn.run_id,
                reason=reason,
                created_at=now,
            )
            updated_turn = context_store.update_context_turn_exclusion(
                turn_id=turn.turn_id,
                excluded_from_context=True,
                excluded_at=now,
                exclusion_reason=reason,
            )
            turn = updated_turn or turn
            audit_ids.append(
                context_store.record_context_event(
                    event_type="context.turn_excluded",
                    session_id=session_id,
                    thread_id=turn.thread_id,
                    run_id=turn.run_id,
                    turn_id=turn.turn_id,
                    created_at=now,
                    payload={"reason": reason, "actor_id": actor_id},
                ).audit_id
            )

        invalidated_memory_ids: list[str] = []
        for memory in existing_memories:
            if memory.status == "invalidated":
                continue
            updated = context_store.update_memory_status(
                memory_id=memory.memory_id,
                status="invalidated",
                updated_at=now,
            )
            if updated is None:
                continue
            invalidated_memory_ids.append(memory.memory_id)
            audit_ids.append(
                context_store.record_context_event(
                    event_type="context.memory_invalidated",
                    session_id=session_id,
                    thread_id=turn.thread_id,
                    run_id=turn.run_id,
                    turn_id=turn.turn_id,
                    created_at=now,
                    payload={
                        "memory_id": memory.memory_id,
                        "memory_type": memory.memory_type,
                        "reason": "source turn excluded from future context",
                    },
                ).audit_id
            )

        if already_excluded and not invalidated_memory_ids and all(chunk.status == "invalidated" for chunk in existing_conversation_chunks):
            snapshot = context_store.get_thread_snapshot(thread_id=turn.thread_id)
            return ContextQuarantineResult(
                action="exclude",
                session_id=session_id,
                turn_id=turn.turn_id,
                run_id=turn.run_id,
                thread_id=turn.thread_id,
                changed=False,
                turn=turn.to_dict(),
                rebuilt_snapshot={
                    "working_memory": dict(snapshot.working_memory) if snapshot is not None else {},
                    "episodic_summary": dict(snapshot.episodic_summary) if snapshot is not None else {},
                },
            )

        invalidated_conversation_ids = self._invalidate_thread_conversation_chunks(thread_id=turn.thread_id, updated_at=now)
        rebuilt_snapshot = self._rebuild_thread_state(
            session_id=session_id,
            thread_id=turn.thread_id,
            run_id=turn.run_id,
            updated_at=now,
        )
        audit_ids.append(
            context_store.record_context_event(
                event_type="context.rebuilt",
                session_id=session_id,
                thread_id=turn.thread_id,
                run_id=turn.run_id,
                turn_id=turn.turn_id,
                created_at=now,
                payload={
                    "reason": "turn exclusion",
                    "rebuilt_working_memory": bool(rebuilt_snapshot.get("working_memory")),
                    "rebuilt_episodic_summary": bool(rebuilt_snapshot.get("episodic_summary")),
                    "invalidated_memory_ids": invalidated_memory_ids,
                    "invalidated_conversation_ids": invalidated_conversation_ids,
                },
            ).audit_id
        )
        self._refresh_memory_index()
        return ContextQuarantineResult(
            action="exclude",
            session_id=session_id,
            turn_id=turn.turn_id,
            run_id=turn.run_id,
            thread_id=turn.thread_id,
            changed=(not already_excluded) or bool(invalidated_memory_ids) or bool(invalidated_conversation_ids),
            turn=turn.to_dict(),
            invalidated_memory_ids=tuple(invalidated_memory_ids),
            invalidated_conversation_ids=tuple(invalidated_conversation_ids),
            rebuilt_snapshot=rebuilt_snapshot,
            audit_event_ids=tuple(audit_ids),
        )

    def hard_delete_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        reason: str = "Hard delete turn",
        actor_id: str = "session_user",
        force: bool = False,
    ) -> ContextQuarantineResult:
        turn = self._turn_or_raise(session_id=session_id, turn_id=turn_id)
        now = self._now()
        blocked_reason = self._hard_delete_block_reason(turn)
        if blocked_reason and not force:
            return ContextQuarantineResult(
                action="hard_delete",
                session_id=session_id,
                turn_id=turn.turn_id,
                run_id=turn.run_id,
                thread_id=turn.thread_id,
                changed=False,
                blocked_reason=blocked_reason,
            )

        deleted_memory_ids: list[str] = []
        for memory in context_store.list_memories_by_provenance(
            turn_id=turn.turn_id,
            run_id=turn.run_id,
            include_inactive=True,
        ):
            if context_store.delete_memory(memory_id=memory.memory_id):
                deleted_memory_ids.append(memory.memory_id)

        deleted_conversation_count = context_store.delete_conversation_chunks_by_provenance(
            turn_id=turn.turn_id,
            run_id=turn.run_id,
            thread_id=turn.thread_id,
        )
        self._session_manager.hard_delete_turn(
            session_id=session_id,
            turn_id=turn.turn_id,
            run_id=turn.run_id,
            reason=reason,
            created_at=now,
        )
        context_store.delete_context_turn_snapshot(turn_id=turn.turn_id)
        audit_ids = [
            context_store.record_context_event(
                event_type="context.turn_hard_deleted",
                session_id=session_id,
                thread_id=turn.thread_id,
                run_id=turn.run_id,
                turn_id=turn.turn_id,
                created_at=now,
                payload={
                    "reason": reason,
                    "actor_id": actor_id,
                    "force": force,
                    "deleted_memory_ids": deleted_memory_ids,
                    "deleted_conversation_count": deleted_conversation_count,
                },
            ).audit_id
        ]
        invalidated_conversation_ids = self._invalidate_thread_conversation_chunks(thread_id=turn.thread_id, updated_at=now)
        rebuilt_snapshot = self._rebuild_thread_state(
            session_id=session_id,
            thread_id=turn.thread_id,
            run_id=turn.run_id,
            updated_at=now,
        )
        audit_ids.append(
            context_store.record_context_event(
                event_type="context.rebuilt",
                session_id=session_id,
                thread_id=turn.thread_id,
                run_id=turn.run_id,
                turn_id=turn.turn_id,
                created_at=now,
                payload={
                    "reason": "hard delete",
                    "rebuilt_working_memory": bool(rebuilt_snapshot.get("working_memory")),
                    "rebuilt_episodic_summary": bool(rebuilt_snapshot.get("episodic_summary")),
                    "invalidated_conversation_ids": invalidated_conversation_ids,
                },
            ).audit_id
        )
        self._refresh_memory_index()
        return ContextQuarantineResult(
            action="hard_delete",
            session_id=session_id,
            turn_id=turn.turn_id,
            run_id=turn.run_id,
            thread_id=turn.thread_id,
            changed=True,
            force=force,
            deleted_memory_ids=tuple(deleted_memory_ids),
            deleted_conversation_count=deleted_conversation_count,
            invalidated_conversation_ids=tuple(invalidated_conversation_ids),
            rebuilt_snapshot=rebuilt_snapshot,
            audit_event_ids=tuple(audit_ids),
        )

    def _turn_or_raise(self, *, session_id: str, turn_id: str) -> ContextTurnSnapshot:
        turn = context_store.get_context_turn_snapshot(turn_id=turn_id, session_id=session_id)
        if turn is None:
            raise ValueError(f"context turn not found: {turn_id}")
        return turn

    def _hard_delete_block_reason(self, turn: ContextTurnSnapshot) -> str:
        if turn.checkpoint_id:
            return "Turn is linked to a checkpoint"
        if turn.run_status in {"resumed", "restoring", "interrupted"}:
            return f"Turn has run_status={turn.run_status}"
        if turn.path_type in {"resumed_hitl", "recovery_path"}:
            return f"Turn belongs to {turn.path_type}"
        return ""

    def _invalidate_thread_conversation_chunks(self, *, thread_id: str, updated_at: str) -> list[str]:
        invalidated_ids: list[str] = []
        for record in context_store.list_conversation_chunks(thread_id=thread_id, limit=500, include_inactive=True):
            if record.status == "invalidated":
                continue
            context_store.update_conversation_chunk_status(
                chunk_id=record.chunk_id,
                status="invalidated",
                updated_at=updated_at,
            )
            invalidated_ids.append(record.chunk_id)
        return invalidated_ids

    def _rebuild_thread_state(
        self,
        *,
        session_id: str,
        thread_id: str,
        run_id: str,
        updated_at: str,
    ) -> dict[str, Any]:
        record = self._session_manager.load_session_record(session_id)
        filtered_messages = self._filtered_messages(record)
        merged_history = self._session_manager.load_session_for_agent(session_id)
        last_user = next(
            (str(item.get("content", "") or "").strip() for item in reversed(filtered_messages) if str(item.get("role", "") or "") == "user"),
            "",
        )
        last_answer = next(
            (str(item.get("content", "") or "").strip() for item in reversed(filtered_messages) if str(item.get("role", "") or "") == "assistant"),
            "",
        )
        working_state = {
            "thread_id": thread_id,
            "session_id": session_id,
            "run_id": run_id,
            "user_message": last_user,
            "history": merged_history,
            "final_answer": last_answer,
            "path_kind": "direct_answer",
            "checkpoint_meta": {"updated_at": updated_at},
        }
        working_memory = build_working_memory(working_state, updated_at=updated_at)
        episodic_summary = self._rebuild_episodic_summary(
            thread_id=thread_id,
            session_id=session_id,
            run_id=run_id,
            filtered_messages=filtered_messages,
            updated_at=updated_at,
        )
        context_store.upsert_thread_snapshot(
            thread_id=thread_id,
            session_id=session_id,
            run_id=run_id,
            working_memory=working_memory,
            episodic_summary=episodic_summary,
            session_memory_state={
                "last_updated_at": updated_at,
                "last_update_reason": "context_rebuild",
                "last_update_trigger": "context_rebuild",
                "last_decision": "update",
                "last_skip_reason": "",
            },
            updated_at=updated_at,
        )
        self._rebuild_conversation_recall(
            session_id=session_id,
            thread_id=thread_id,
            filtered_messages=filtered_messages,
            updated_at=updated_at,
        )
        return {
            "working_memory": working_memory.to_dict(),
            "episodic_summary": episodic_summary.to_dict(),
            "message_count": len(filtered_messages),
        }

    def _rebuild_episodic_summary(
        self,
        *,
        thread_id: str,
        session_id: str,
        run_id: str,
        filtered_messages: list[dict[str, Any]],
        updated_at: str,
    ) -> EpisodicSummary:
        previous: dict[str, Any] | None = None
        history_so_far: list[dict[str, str]] = []
        for message in filtered_messages:
            role = str(message.get("role", "") or "").strip()
            content = str(message.get("content", "") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            history_so_far.append({"role": role, "content": content})
            if role != "assistant":
                continue
            previous = build_episodic_summary(
                {
                    "thread_id": thread_id,
                    "session_id": session_id,
                    "run_id": str(message.get("run_id", "") or run_id),
                    "history": list(history_so_far),
                    "final_answer": content,
                    "path_kind": "direct_answer",
                    "checkpoint_meta": {"updated_at": updated_at},
                },
                previous=previous,
                updated_at=updated_at,
            ).to_dict()
        if previous is None:
            return build_episodic_summary(
                {
                    "thread_id": thread_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "history": [{"role": str(item.get("role", "") or ""), "content": str(item.get("content", "") or "")} for item in filtered_messages],
                    "checkpoint_meta": {"updated_at": updated_at},
                },
                previous=None,
                updated_at=updated_at,
            )
        return EpisodicSummary(
            thread_id=str(previous.get("thread_id", "") or thread_id),
            summary_version=int(previous.get("summary_version", 1) or 1),
            key_facts=tuple(str(item) for item in previous.get("key_facts", []) or []),
            completed_subtasks=tuple(str(item) for item in previous.get("completed_subtasks", []) or []),
            rejected_paths=tuple(str(item) for item in previous.get("rejected_paths", []) or []),
            important_decisions=tuple(str(item) for item in previous.get("important_decisions", []) or []),
            important_artifacts=tuple(str(item) for item in previous.get("important_artifacts", []) or []),
            open_loops=tuple(str(item) for item in previous.get("open_loops", []) or []),
            updated_at=str(previous.get("updated_at", "") or updated_at),
        )

    def _rebuild_conversation_recall(
        self,
        *,
        session_id: str,
        thread_id: str,
        filtered_messages: list[dict[str, Any]],
        updated_at: str,
    ) -> None:
        for index, message in enumerate(filtered_messages):
            role = str(message.get("role", "") or "").strip()
            content = str(message.get("content", "") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            context_store.insert_conversation_chunk(
                thread_id=thread_id,
                session_id=session_id,
                run_id=str(message.get("run_id", "") or ""),
                role=role,
                source_message_id=str(message.get("message_id", "") or f"{thread_id}:{index}"),
                snippet=content[:320],
                summary=content[:180],
                tags=_tags_for(content),
                metadata={"index": index, "rebuilt": True},
                source_turn_ids=[str(message.get("turn_id", "") or "")] if str(message.get("turn_id", "") or "").strip() else [],
                source_run_ids=[str(message.get("run_id", "") or "")] if str(message.get("run_id", "") or "").strip() else [],
                source_memory_ids=[],
                generated_by="context.rebuild",
                generated_at=updated_at,
                status="active",
                created_at=updated_at,
            )

    def _filtered_messages(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        excluded_turn_ids = {str(item) for item in record.get("excluded_turn_ids", []) or []}
        excluded_run_ids = {str(item) for item in record.get("excluded_run_ids", []) or []}
        filtered: list[dict[str, Any]] = []
        for message in list(record.get("messages", []) or []):
            role = str(message.get("role", "") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            if bool(message.get("excluded_from_context")):
                continue
            turn_id = str(message.get("turn_id", "") or "")
            run_id = str(message.get("run_id", "") or "")
            if turn_id and turn_id in excluded_turn_ids:
                continue
            if run_id and run_id in excluded_run_ids:
                continue
            filtered.append(dict(message))
        return filtered

    def _refresh_memory_index(self) -> None:
        manifests = [
            item
            for item in context_store.list_memory_manifests(limit=200)
            if item.memory_type != "session_episode" and item.status not in {"dropped", "invalidated"}
        ]
        context_store.write_memory_index(render_memory_index(manifests))

    def _now(self) -> str:
        value = str(self._now_factory() or "").strip()
        return value
