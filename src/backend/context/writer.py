from __future__ import annotations

from typing import Any

from src.backend.context.episodic_memory import build_episodic_summary
from src.backend.context.working_memory import build_working_memory


class ContextWriter:
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
        return {
            "working_memory": working_memory.to_dict(),
            "episodic_summary": episodic_summary.to_dict(),
            "checkpoint_meta": checkpoint_meta,
        }
