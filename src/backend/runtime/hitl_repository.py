from __future__ import annotations

from pathlib import Path
from typing import Any


class SqliteHitlRepository:
    def __init__(self, *, base_dir: Path, store: Any | None = None) -> None:
        if store is None:
            from src.backend.orchestration.checkpointing import checkpoint_store  # pylint: disable=import-outside-toplevel

            store = checkpoint_store
        self._store = store
        self.configure_for_base_dir(base_dir)

    @property
    def saver(self) -> Any:
        return self._store.saver

    def configure_for_base_dir(self, base_dir: Path) -> None:
        self._store.configure_for_base_dir(base_dir)

    def close(self) -> None:
        self._store.close()

    def thread_id_for(self, session_id: str | None, run_id: str | None) -> str:
        return self._store.thread_id_for(session_id=session_id, run_id=run_id)

    def list_thread_checkpoints(self, thread_id: str):
        return self._store.list_thread_checkpoints(thread_id)

    def get_checkpoint(self, thread_id: str, checkpoint_id: str):
        return self._store.get_checkpoint(thread_id=thread_id, checkpoint_id=checkpoint_id)

    def latest_checkpoint(self, thread_id: str):
        return self._store.latest_checkpoint(thread_id=thread_id)

    def pending_hitl(self, thread_id: str):
        return self._store.pending_hitl(thread_id=thread_id)

    def list_pending_hitl(self, limit: int = 50):
        return self._store.list_pending_hitl(limit=limit)

    def list_hitl_requests(self, thread_id: str):
        return self._store.list_hitl_requests(thread_id=thread_id)

    def get_hitl_request(
        self,
        *,
        thread_id: str | None = None,
        checkpoint_id: str | None = None,
        request_id: str | None = None,
    ):
        return self._store.get_hitl_request(thread_id=thread_id, checkpoint_id=checkpoint_id, request_id=request_id)

    def get_hitl_decision(
        self,
        *,
        thread_id: str | None = None,
        checkpoint_id: str | None = None,
        request_id: str | None = None,
    ):
        return self._store.get_hitl_decision(thread_id=thread_id, checkpoint_id=checkpoint_id, request_id=request_id)

    def record_pending_hitl(self, request):
        return self._store.record_pending_hitl(request)

    def record_hitl_decision(
        self,
        *,
        thread_id: str,
        checkpoint_id: str,
        decision: str,
        actor_id: str,
        actor_type: str,
        decided_at: str,
        resume_source: str,
        approved_input_snapshot: dict[str, Any] | None = None,
        edited_input_snapshot: dict[str, Any] | None = None,
        rejected_input_snapshot: dict[str, Any] | None = None,
    ):
        return self._store.record_hitl_decision(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            decision=decision,
            actor_id=actor_id,
            actor_type=actor_type,
            decided_at=decided_at,
            resume_source=resume_source,
            approved_input_snapshot=approved_input_snapshot,
            edited_input_snapshot=edited_input_snapshot,
            rejected_input_snapshot=rejected_input_snapshot,
        )


__all__ = ["SqliteHitlRepository"]
