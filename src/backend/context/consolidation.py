from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Any

from src.backend.context.governance import RULES, rule_for
from src.backend.context.manifest import render_memory_index
from src.backend.context.models import ConsolidationRunSummary, MemoryCandidate
from src.backend.context.policies import autodream_policy, thread_namespace
from src.backend.context.store import context_store


def _iso_to_datetime(value: str) -> datetime | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


class AutoDreamConsolidator:
    _gate_lock = RLock()
    _active_threads: set[str] = set()
    _scan_timestamps: dict[str, float] = {}

    def __init__(self, *, base_dir: Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir is not None else None

    def should_trigger(self, *, trigger: str, thread_id: str | None = None) -> bool:
        decision = self._gate_decision(trigger=trigger, thread_id=thread_id)
        return bool(decision["allowed"])

    def _gate_decision(self, *, trigger: str, thread_id: str | None = None) -> dict[str, Any]:
        policy = autodream_policy()
        normalized_thread = str(thread_id or "").strip()
        if trigger == "manual":
            return {"allowed": True, "reason": "manual_trigger", "stats": {"thread_id": normalized_thread}}
        if not policy.enabled:
            return {"allowed": False, "reason": "feature_gate_disabled", "stats": {"thread_id": normalized_thread}}

        thread_key = normalized_thread or "global"
        with self._gate_lock:
            last_scan = self._scan_timestamps.get(thread_key, 0.0)
            now_scan = monotonic()
            if now_scan - last_scan < policy.scan_throttle_seconds:
                return {
                    "allowed": False,
                    "reason": "scan_throttle",
                    "stats": {"thread_id": normalized_thread, "seconds_until_retry": round(policy.scan_throttle_seconds - (now_scan - last_scan), 2)},
                }
            self._scan_timestamps[thread_key] = now_scan
            if thread_key in self._active_threads:
                return {"allowed": False, "reason": "lock_held", "stats": {"thread_id": normalized_thread}}

        latest = context_store.latest_consolidation_run(thread_id=normalized_thread or None)
        if latest is not None:
            latest_dt = _iso_to_datetime(latest.completed_at or latest.created_at)
            if latest_dt is not None:
                minutes_since = (datetime.now(timezone.utc) - latest_dt.astimezone(timezone.utc)).total_seconds() / 60.0
                if minutes_since < policy.min_minutes_between_runs:
                    return {
                        "allowed": False,
                        "reason": "time_gate",
                        "stats": {"thread_id": normalized_thread, "minutes_since_last_run": round(minutes_since, 2)},
                    }
        recent_episodes = context_store.list_memory_manifests(kind="episodic", namespace=normalized_thread or None, limit=24)
        if not recent_episodes:
            return {"allowed": False, "reason": "no_recent_episodes", "stats": {"thread_id": normalized_thread}}

        latest_completed = latest.completed_at if latest is not None else ""
        updated_after = [item for item in recent_episodes if not latest_completed or item.updated_at > latest_completed]
        stable_signal_count = 0
        for manifest in updated_after:
            record = context_store.get_memory(memory_id=manifest.memory_id)
            if record is None:
                continue
            stable_signal_count += len(list(record.metadata.get("stable_candidates", []) or []))
        if len(updated_after) < policy.min_recent_episodes or stable_signal_count < policy.min_stable_signal_count:
            return {
                "allowed": False,
                "reason": "recent_signal_threshold",
                "stats": {
                    "thread_id": normalized_thread,
                    "recent_episode_count": len(updated_after),
                    "stable_signal_count": stable_signal_count,
                },
            }

        return {
            "allowed": True,
            "reason": "recent_signal_threshold_met",
            "stats": {
                "thread_id": normalized_thread,
                "recent_episode_count": len(updated_after),
                "stable_signal_count": stable_signal_count,
            },
        }

    def _acquire_lock(self, thread_id: str | None) -> bool:
        thread_key = str(thread_id or "").strip() or "global"
        with self._gate_lock:
            if thread_key in self._active_threads:
                return False
            self._active_threads.add(thread_key)
            return True

    def _release_lock(self, thread_id: str | None) -> None:
        thread_key = str(thread_id or "").strip() or "global"
        with self._gate_lock:
            self._active_threads.discard(thread_key)

    def consolidate(
        self,
        *,
        trigger: str,
        thread_id: str | None = None,
        started_at: str,
        force: bool = False,
    ) -> ConsolidationRunSummary:
        gate = self._gate_decision(trigger=trigger, thread_id=thread_id)
        if not force and not gate["allowed"]:
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
                    "notes": [f"consolidation skipped: {gate['reason']}"],
                    "stats": dict(gate.get("stats", {})),
                },
            )

        if not self._acquire_lock(thread_id):
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
                    "notes": ["consolidation skipped: lock already held"],
                    "stats": {"thread_id": thread_id or "", "lock_state": "busy"},
                },
            )

        try:
            episode_namespace = (
                thread_id
                if thread_id and thread_id.startswith("thread:")
                else thread_namespace(thread_id or "") if thread_id else None
            )
            episodes = context_store.list_memories(kind="episodic", namespace=episode_namespace, limit=40)
            active_manifests = context_store.list_memory_manifests(limit=200)

            promoted_ids: list[str] = []
            superseded_ids: list[str] = []
            stale_ids: list[str] = []
            dropped_ids: list[str] = []
            conflict_ids: list[str] = []
            notes: list[str] = [f"gate={gate['reason']}"]

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
                        body=dict(payload.get("body", {}) or existing.body),
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
                    body=dict(payload.get("body", {}) or {}),
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
                notes.append(f"promoted {len(promoted_ids)} stable memory candidates")
            if stale_ids:
                notes.append(f"stale manifests observed: {len(stale_ids)}")
            if dropped_ids:
                notes.append(f"dropped low-value episodic memories: {len(dropped_ids)}")
            if conflict_ids:
                notes.append(f"conflicting memories surfaced: {len(conflict_ids)}")
            if len(notes) == 1:
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
                        **dict(gate.get("stats", {})),
                        "episodes_seen": len(episodes),
                        "candidate_groups": len(candidate_counts),
                        "index_entries": len(active_index_manifests),
                    },
                },
            )
        finally:
            self._release_lock(thread_id)

    def latest(self) -> ConsolidationRunSummary | None:
        return context_store.latest_consolidation_run()

    def list_runs(self, *, limit: int = 10) -> list[ConsolidationRunSummary]:
        return context_store.list_consolidation_runs(limit=limit)
