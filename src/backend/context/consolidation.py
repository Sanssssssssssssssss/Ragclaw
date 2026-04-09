from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from src.backend.context.governance import RULES, rule_for
from src.backend.context.manifest import render_memory_index
from src.backend.context.models import ConsolidationRunSummary, MemoryCandidate, MemoryManifest
from src.backend.context.policies import thread_namespace
from src.backend.context.store import context_store


class AutoDreamConsolidator:
    def __init__(self, *, base_dir: Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir is not None else None

    def should_trigger(self, *, trigger: str, thread_id: str | None = None) -> bool:
        if trigger == "manual":
            return True
        latest = context_store.latest_consolidation_run()
        recent_episodes = context_store.list_memory_manifests(kind="episodic", namespace=None, limit=12)
        if not recent_episodes:
            return False
        if latest is None:
            return True
        latest_completed = latest.completed_at or latest.created_at
        updated_after = [item for item in recent_episodes if item.updated_at > latest_completed]
        return len(updated_after) >= 2

    def consolidate(
        self,
        *,
        trigger: str,
        thread_id: str | None = None,
        started_at: str,
        force: bool = False,
    ) -> ConsolidationRunSummary:
        if not force and not self.should_trigger(trigger=trigger, thread_id=thread_id):
            return context_store.record_consolidation_run(
                trigger=trigger,
                thread_id=thread_id,
                status="skipped",
                created_at=started_at,
                completed_at=started_at,
                summary={
                    "promoted_memory_ids": [],
                    "superseded_memory_ids": [],
                    "stale_memory_ids": [],
                    "dropped_memory_ids": [],
                    "conflict_memory_ids": [],
                    "notes": ["consolidation skipped by trigger threshold"],
                    "stats": {"episodes_seen": 0},
                },
            )

        episode_namespace = thread_id if thread_id and thread_id.startswith("thread:") else thread_namespace(thread_id or "") if thread_id else None
        episodes = context_store.list_memories(kind="episodic", namespace=episode_namespace, limit=40)
        active_manifests = context_store.list_memory_manifests(limit=200)

        promoted_ids: list[str] = []
        superseded_ids: list[str] = []
        stale_ids: list[str] = []
        dropped_ids: list[str] = []
        conflict_ids: list[str] = []
        notes: list[str] = []

        candidate_counts: Counter[str] = Counter()
        candidate_payloads: dict[str, dict[str, Any]] = {}
        for episode in episodes:
            stable_candidates = list(episode.metadata.get("stable_candidates", []) or [])
            for payload in stable_candidates:
                fingerprint = str(payload.get("fingerprint", "") or "").strip()
                if not fingerprint:
                    continue
                candidate_counts[fingerprint] += 1
                candidate_payloads[fingerprint] = dict(payload)

        for fingerprint, seen_count in candidate_counts.items():
            payload = candidate_payloads[fingerprint]
            memory_type = str(payload.get("memory_type", "") or "").strip()
            if memory_type not in RULES:
                continue
            rule = rule_for(memory_type)  # type: ignore[arg-type]
            if seen_count < rule.promotion_threshold:
                continue
            existing = context_store.get_memory_by_fingerprint(fingerprint=fingerprint)
            if existing is not None:
                updated = context_store.update_memory(
                    memory_id=existing.memory_id,
                    updated_at=started_at,
                    metadata={**existing.metadata, "consolidated_at": started_at, "consolidated_hits": seen_count},
                )
                if updated is not None:
                    promoted_ids.append(updated.memory_id)
                continue
            candidate = MemoryCandidate(
                kind=rule.kind,
                memory_type=memory_type,  # type: ignore[arg-type]
                scope=rule.scope,
                namespace=str(payload.get("namespace", "") or ""),
                title=str(payload.get("title", "") or rule.allow_title_prefix),
                content=str(payload.get("summary", "") or ""),
                summary=str(payload.get("summary", "") or ""),
                tags=("autodream", memory_type),
                metadata={"consolidated_at": started_at, "consolidated_hits": seen_count},
                source="autodream",
                created_at=started_at,
                updated_at=started_at,
                confidence=float(payload.get("confidence", 0.6) or 0.6),
                stale_after="",
                applicability={"prompt_paths": []},
                direct_prompt=bool(payload.get("direct_prompt", False)),
                promotion_priority=rule.promotion_priority,
                fingerprint=fingerprint,
                conflict_key=str(payload.get("conflict_key", "") or ""),
            )
            record = context_store.insert_memory_candidate(candidate)
            promoted_ids.append(record.memory_id)

        for manifest in active_manifests:
            if manifest.status == "superseded":
                superseded_ids.append(manifest.memory_id)
            if manifest.status == "stale":
                stale_ids.append(manifest.memory_id)
            if manifest.conflict_flag:
                conflict_ids.append(manifest.memory_id)

        low_value_episodes = [item for item in episodes if item.confidence < 0.45 and item.freshness == "stale"]
        for episode in low_value_episodes:
            updated = context_store.update_memory_status(memory_id=episode.memory_id, status="dropped", updated_at=started_at)
            if updated is not None:
                dropped_ids.append(updated.memory_id)

        active_index_manifests = [
            manifest
            for manifest in context_store.list_memory_manifests(limit=200)
            if manifest.status == "active" and manifest.memory_type != "session_episode"
        ]
        context_store.write_memory_index(render_memory_index(active_index_manifests))

        if promoted_ids:
            notes.append(f"promoted {len(promoted_ids)} stable memory candidates from recent session episodes")
        if stale_ids:
            notes.append(f"marked {len(stale_ids)} memories as stale")
        if dropped_ids:
            notes.append(f"dropped {len(dropped_ids)} low-value episodic memories")
        if conflict_ids:
            notes.append(f"surfaced {len(conflict_ids)} conflicting memories for audit")
        if not notes:
            notes.append("memory set already compact; manifests refreshed only")

        return context_store.record_consolidation_run(
            trigger=trigger,
            thread_id=thread_id,
            status="completed",
            created_at=started_at,
            completed_at=started_at,
            summary={
                "promoted_memory_ids": promoted_ids,
                "superseded_memory_ids": superseded_ids,
                "stale_memory_ids": stale_ids,
                "dropped_memory_ids": dropped_ids,
                "conflict_memory_ids": conflict_ids,
                "notes": notes,
                "stats": {
                    "episodes_seen": len(episodes),
                    "candidate_groups": len(candidate_counts),
                    "index_entries": len(active_index_manifests),
                },
            },
        )

    def latest(self) -> ConsolidationRunSummary | None:
        return context_store.latest_consolidation_run()

    def list_runs(self, *, limit: int = 10) -> list[ConsolidationRunSummary]:
        return context_store.list_consolidation_runs(limit=limit)
