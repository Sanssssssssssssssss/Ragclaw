"""Append-only run trace persistence for harness events."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.backend.observability.types import HarnessEvent, RunMetadata, RunOutcome


@dataclass(frozen=True)
class RunTracePaths:
    run_id: str
    trace_path: Path
    summary_path: Path


class JsonlRunTraceRepository:
    """Persist append-only run traces under backend/storage/runs/."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._event_ids: dict[str, set[str]] = {}

    def _trace_path(self, run_id: str) -> Path:
        return self.root_dir / f"{run_id}.jsonl"

    def _summary_path(self, run_id: str) -> Path:
        return self.root_dir / f"{run_id}.summary.json"

    def paths_for(self, run_id: str) -> RunTracePaths:
        return RunTracePaths(
            run_id=run_id,
            trace_path=self._trace_path(run_id),
            summary_path=self._summary_path(run_id),
        )

    def create_run(self, metadata: RunMetadata) -> RunTracePaths:
        paths = self.paths_for(metadata.run_id)
        if paths.trace_path.exists():
            raise FileExistsError(f"trace already exists for run_id={metadata.run_id}")
        self._append_jsonl(
            paths.trace_path,
            {
                "record_type": "run_metadata",
                "run_id": metadata.run_id,
                "payload": metadata.to_dict(),
            },
            create=True,
        )
        self._event_ids[metadata.run_id] = set()
        return paths

    def append_event(self, event: HarnessEvent) -> None:
        paths = self.paths_for(event.run_id)
        if not paths.trace_path.exists():
            raise FileNotFoundError(f"trace does not exist for run_id={event.run_id}")
        if paths.summary_path.exists():
            raise RuntimeError(f"trace already finalized for run_id={event.run_id}")
        known_ids = self._event_ids.setdefault(event.run_id, self._load_event_ids(event.run_id))
        if event.event_id in known_ids:
            return
        self._append_jsonl(
            paths.trace_path,
            {
                "record_type": "event",
                "run_id": event.run_id,
                "payload": event.to_dict(),
            },
        )
        known_ids.add(event.event_id)

    def finalize_run(self, run_id: str, outcome: RunOutcome) -> RunTracePaths:
        paths = self.paths_for(run_id)
        if not paths.trace_path.exists():
            raise FileNotFoundError(f"trace does not exist for run_id={run_id}")
        if paths.summary_path.exists():
            raise RuntimeError(f"trace already finalized for run_id={run_id}")

        self._append_jsonl(
            paths.trace_path,
            {
                "record_type": "run_outcome",
                "run_id": run_id,
                "payload": outcome.to_dict(),
            },
        )
        self._write_json_atomic(
            paths.summary_path,
            {
                "run_id": run_id,
                "status": outcome.status,
                "route_intent": outcome.route_intent,
                "used_skill": outcome.used_skill,
                "tool_names": list(outcome.tool_names),
                "retrieval_sources": list(outcome.retrieval_sources),
                "error_message": outcome.error_message,
                "completed_at": outcome.completed_at,
            },
        )
        return paths

    def read_trace(self, run_id: str) -> dict[str, Any]:
        paths = self.paths_for(run_id)
        if not paths.trace_path.exists():
            raise FileNotFoundError(f"trace does not exist for run_id={run_id}")

        metadata: dict[str, Any] | None = None
        events: list[dict[str, Any]] = []
        outcome: dict[str, Any] | None = None
        with paths.trace_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                record = json.loads(stripped)
                record_type = str(record.get("record_type", "") or "")
                payload = record.get("payload", {})
                if record_type == "run_metadata":
                    metadata = payload
                elif record_type == "event":
                    events.append(payload)
                elif record_type == "run_outcome":
                    outcome = payload

        return {
            "run_id": run_id,
            "metadata": metadata,
            "events": events,
            "outcome": outcome,
            "event_count": len(events),
            "event_checksum": _event_checksum(events),
            "summary_path": str(paths.summary_path) if paths.summary_path.exists() else "",
            "trace_path": str(paths.trace_path),
        }

    def list_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for summary_path in sorted(
            self.root_dir.glob("*.summary.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            run_id = str(payload.get("run_id", "") or "").strip()
            if not run_id:
                continue
            trace = self.read_trace(run_id)
            metadata = trace.get("metadata") or {}
            outcome = trace.get("outcome") or {}
            if session_id and str(metadata.get("session_id", "") or "") != session_id:
                continue
            if status and str(outcome.get("status", "") or "") != status:
                continue
            items.append(_trace_listing(trace))
        return items[offset : offset + limit]

    def list_run_events(self, run_id: str, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        trace = self.read_trace(run_id)
        return list((trace.get("events") or [])[offset : offset + limit])

    def stats(self) -> dict[str, Any]:
        total_runs = 0
        completed_runs = 0
        failed_runs = 0
        by_source: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for item in self.list_runs(limit=10_000):
            total_runs += 1
            source = str(item.get("source", "") or "unknown")
            by_source[source] = by_source.get(source, 0) + 1
            state = str(item.get("status", "") or "unknown")
            by_status[state] = by_status.get(state, 0) + 1
            if state == "completed":
                completed_runs += 1
            elif state == "failed":
                failed_runs += 1
        return {
            "total_runs": total_runs,
            "completed_runs": completed_runs,
            "failed_runs": failed_runs,
            "by_source": by_source,
            "by_status": by_status,
        }

    def _append_jsonl(self, path: Path, record: dict[str, Any], *, create: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not create and not path.exists():
            raise FileNotFoundError(path)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _load_event_ids(self, run_id: str) -> set[str]:
        trace_path = self._trace_path(run_id)
        if not trace_path.exists():
            return set()
        event_ids: set[str] = set()
        with trace_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                record = json.loads(stripped)
                if str(record.get("record_type", "") or "") != "event":
                    continue
                payload = dict(record.get("payload") or {})
                event_id = str(payload.get("event_id", "") or "").strip()
                if event_id:
                    event_ids.add(event_id)
        return event_ids

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_handle = temp_path.open("r+", encoding="utf-8")
        try:
            temp_handle.flush()
            os.fsync(temp_handle.fileno())
        finally:
            temp_handle.close()
        os.replace(temp_path, path)


class RunTraceStore(JsonlRunTraceRepository):
    """Backward-compatible alias preserving the historical RunTraceStore name."""

    pass


def _event_checksum(events: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for event in events:
        digest.update(json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _trace_listing(trace: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(trace.get("metadata") or {})
    outcome = dict(trace.get("outcome") or {})
    return {
        "run_id": str(trace.get("run_id", "") or ""),
        "session_id": metadata.get("session_id"),
        "thread_id": metadata.get("thread_id"),
        "user_message": str(metadata.get("user_message", "") or ""),
        "source": str(metadata.get("source", "") or ""),
        "started_at": str(metadata.get("started_at", "") or ""),
        "orchestration_engine": str(metadata.get("orchestration_engine", "") or ""),
        "checkpoint_id": str(outcome.get("checkpoint_id", metadata.get("checkpoint_id", "")) or ""),
        "resume_source": str(outcome.get("resume_source", metadata.get("resume_source", "")) or ""),
        "run_status": str(outcome.get("run_status", metadata.get("run_status", "")) or ""),
        "status": str(outcome.get("status", "") or ""),
        "route_intent": str(outcome.get("route_intent", "") or ""),
        "used_skill": str(outcome.get("used_skill", "") or ""),
        "event_count": int(trace.get("event_count", 0) or 0),
        "event_checksum": str(trace.get("event_checksum", "") or ""),
        "jsonl_trace_path": str(trace.get("trace_path", "") or ""),
        "jsonl_summary_path": str(trace.get("summary_path", "") or ""),
        "completed_at": str(outcome.get("completed_at", "") or ""),
        "error_message": str(outcome.get("error_message", "") or ""),
    }


__all__ = [
    "JsonlRunTraceRepository",
    "RunTracePaths",
    "RunTraceStore",
    "_event_checksum",
    "_trace_listing",
]
