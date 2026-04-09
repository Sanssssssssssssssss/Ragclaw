from __future__ import annotations

from pathlib import Path
from typing import Any

from src.backend.context.consolidation import AutoDreamConsolidator
from src.backend.context.episodic_memory import build_episodic_summary
from src.backend.context.governance import extract_memory_candidates
from src.backend.context.manifest import render_memory_index
from src.backend.context.policies import thread_namespace
from src.backend.context.recall import conversation_recall
from src.backend.context.store import context_store
from src.backend.context.working_memory import build_working_memory


class ContextWriter:
    def __init__(self, *, base_dir: Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir is not None else None
        self._autodream = AutoDreamConsolidator(base_dir=self._base_dir)

    def snapshot(self, state: dict[str, Any], *, updated_at: str = "") -> dict[str, Any]:
        previous_summary = state.get("episodic_summary")
        working_memory = build_working_memory(state, updated_at=updated_at)
        episodic_summary = build_episodic_summary(
            state,
            previous=previous_summary if isinstance(previous_summary, dict) else None,
            updated_at=updated_at,
        )
        checkpoint_meta = dict(state.get("checkpoint_meta", {}) or {})
        if updated_at:
            checkpoint_meta["updated_at"] = updated_at

        latest_consolidation = None
        if working_memory.thread_id and updated_at:
            try:
                context_store.upsert_thread_snapshot(
                    thread_id=working_memory.thread_id,
                    session_id=str(state.get("session_id", "") or "") or None,
                    run_id=str(state.get("run_id", "") or ""),
                    working_memory=working_memory,
                    episodic_summary=episodic_summary,
                    updated_at=updated_at,
                )
                conversation_recall.record(state=state, updated_at=updated_at)
                self._promote_memories(
                    state=state,
                    working_memory=working_memory,
                    episodic_summary=episodic_summary,
                    updated_at=updated_at,
                )
                self._refresh_memory_index()
                latest_consolidation = self._maybe_consolidate(
                    state=state,
                    thread_id=working_memory.thread_id,
                    updated_at=updated_at,
                )
            except Exception:
                latest_consolidation = None

        payload = {
            "working_memory": working_memory.to_dict(),
            "episodic_summary": episodic_summary.to_dict(),
            "checkpoint_meta": checkpoint_meta,
        }
        if latest_consolidation is not None:
            payload["latest_consolidation"] = latest_consolidation.to_dict()
        return payload

    def _promote_memories(
        self,
        *,
        state: dict[str, Any],
        working_memory,
        episodic_summary,
        updated_at: str,
    ) -> None:
        candidates = extract_memory_candidates(
            state=state,
            working_memory=working_memory,
            episodic_summary=episodic_summary,
            base_dir=self._base_dir,
            updated_at=updated_at,
        )
        for candidate in candidates:
            context_store.insert_memory_candidate(candidate)

    def _refresh_memory_index(self) -> None:
        manifests = [
            item
            for item in context_store.list_memory_manifests(limit=200)
            if item.memory_type != "session_episode" and item.status != "dropped"
        ]
        context_store.write_memory_index(render_memory_index(manifests))

    def _maybe_consolidate(
        self,
        *,
        state: dict[str, Any],
        thread_id: str,
        updated_at: str,
    ):
        trigger = self._trigger_for(state)
        if not trigger:
            return None
        normalized_thread = thread_namespace(thread_id)
        if not self._autodream.should_trigger(trigger=trigger, thread_id=normalized_thread):
            return None
        return self._autodream.consolidate(
            trigger=trigger,
            thread_id=normalized_thread,
            started_at=updated_at,
        )

    def _trigger_for(self, state: dict[str, Any]) -> str | None:
        run_status = str(state.get("checkpoint_meta", {}).get("run_status", "") or "").strip().lower()
        if str(state.get("final_answer", "") or "").strip():
            return "turn_end"
        if run_status in {"resumed", "restoring", "interrupted"}:
            return "checkpoint"
        if len(list(state.get("capability_results", []) or [])) >= 3:
            return "threshold"
        return None
