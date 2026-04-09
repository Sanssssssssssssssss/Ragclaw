from __future__ import annotations

from pathlib import Path
from typing import Any

from src.backend.context.episodic_memory import build_episodic_summary
from src.backend.context.policies import fingerprint_for, promotion_candidates
from src.backend.context.procedural_memory import procedural_memory
from src.backend.context.semantic_memory import semantic_memory
from src.backend.context.store import context_store
from src.backend.context.working_memory import build_working_memory


class ContextWriter:
    def __init__(self, *, base_dir: Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir is not None else None

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
                self._promote_memories(
                    state=state,
                    working_memory=working_memory,
                    episodic_summary=episodic_summary,
                    updated_at=updated_at,
                )
            except Exception:
                pass

        return {
            "working_memory": working_memory.to_dict(),
            "episodic_summary": episodic_summary.to_dict(),
            "checkpoint_meta": checkpoint_meta,
        }

    def _promote_memories(
        self,
        *,
        state: dict[str, Any],
        working_memory,
        episodic_summary,
        updated_at: str,
    ) -> None:
        candidates = promotion_candidates(
            state=state,
            working_memory=working_memory,
            episodic_summary=episodic_summary,
            base_dir=self._base_dir,
            updated_at=updated_at,
        )
        for candidate in candidates.get("semantic", []):
            semantic_memory.insert(
                namespace=str(candidate["namespace"]),
                title=str(candidate["title"]),
                content=str(candidate["content"]),
                summary=str(candidate.get("summary", "")),
                tags=tuple(candidate.get("tags", []) or ()),
                metadata=dict(candidate.get("metadata", {}) or {}),
                source=str(candidate.get("source", "")),
                created_at=updated_at,
                fingerprint=fingerprint_for(
                    "semantic",
                    str(candidate["namespace"]),
                    str(candidate["content"]),
                    tuple(candidate.get("tags", []) or ()),
                ),
            )
        for candidate in candidates.get("procedural", []):
            procedural_memory.insert(
                namespace=str(candidate["namespace"]),
                title=str(candidate["title"]),
                content=str(candidate["content"]),
                summary=str(candidate.get("summary", "")),
                tags=tuple(candidate.get("tags", []) or ()),
                metadata=dict(candidate.get("metadata", {}) or {}),
                source=str(candidate.get("source", "")),
                created_at=updated_at,
                fingerprint=fingerprint_for(
                    "procedural",
                    str(candidate["namespace"]),
                    str(candidate["content"]),
                    tuple(candidate.get("tags", []) or ()),
                ),
            )
