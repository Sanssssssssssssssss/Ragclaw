"""Append-only run trace persistence for harness events."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.types import HarnessEvent, RunMetadata, RunOutcome


@dataclass(frozen=True)
class RunTracePaths:
    run_id: str
    trace_path: Path
    summary_path: Path


class RunTraceStore:
    """Persist append-only run traces under backend/storage/runs/."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

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
        return paths

    def append_event(self, event: HarnessEvent) -> None:
        paths = self.paths_for(event.run_id)
        if not paths.trace_path.exists():
            raise FileNotFoundError(f"trace does not exist for run_id={event.run_id}")
        if paths.summary_path.exists():
            raise RuntimeError(f"trace already finalized for run_id={event.run_id}")
        self._append_jsonl(
            paths.trace_path,
            {
                "record_type": "event",
                "run_id": event.run_id,
                "payload": event.to_dict(),
            },
        )

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
            "summary_path": str(paths.summary_path) if paths.summary_path.exists() else "",
            "trace_path": str(paths.trace_path),
        }

    def _append_jsonl(self, path: Path, record: dict[str, Any], *, create: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not create and not path.exists():
            raise FileNotFoundError(path)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

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

