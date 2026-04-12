from __future__ import annotations

from pathlib import Path
from typing import Any

from src.backend.observability.postgres_trace_store import PostgresRunTraceRepository, RunTraceParity
from src.backend.observability.trace_store import JsonlRunTraceRepository, RunTracePaths, _event_checksum
from src.backend.observability.types import HarnessEvent, RunMetadata, RunOutcome


class DualWriteRunTraceRepository:
    """Write every run trace to JSONL and Postgres, then persist parity snapshots."""

    def __init__(
        self,
        *,
        jsonl_repository: JsonlRunTraceRepository,
        postgres_repository: PostgresRunTraceRepository,
    ) -> None:
        self._jsonl = jsonl_repository
        self._postgres = postgres_repository

    def paths_for(self, run_id: str) -> RunTracePaths:
        return self._jsonl.paths_for(run_id)

    def create_run(self, metadata: RunMetadata) -> RunTracePaths:
        jsonl_paths = self._jsonl.create_run(metadata)
        self._postgres.create_run(metadata)
        self._postgres.set_jsonl_paths(
            metadata.run_id,
            trace_path=jsonl_paths.trace_path,
            summary_path=jsonl_paths.summary_path,
        )
        return jsonl_paths

    def append_event(self, event: HarnessEvent) -> None:
        self._jsonl.append_event(event)
        self._postgres.append_event(event)

    def finalize_run(self, run_id: str, outcome: RunOutcome) -> RunTracePaths:
        jsonl_paths = self._jsonl.finalize_run(run_id, outcome)
        self._postgres.finalize_run(run_id, outcome)
        self._record_parity(run_id)
        return jsonl_paths

    def read_trace(self, run_id: str) -> dict[str, Any]:
        return self._jsonl.read_trace(run_id)

    def list_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._postgres.list_runs(limit=limit, offset=offset, session_id=session_id, status=status)

    def list_run_events(self, run_id: str, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        return self._postgres.list_run_events(run_id, limit=limit, offset=offset)

    def stats(self) -> dict[str, Any]:
        return self._postgres.stats()

    def parity_report(self, run_id: str) -> dict[str, Any] | None:
        return self._postgres.parity_report(run_id)

    def _record_parity(self, run_id: str) -> RunTraceParity:
        jsonl_trace = self._jsonl.read_trace(run_id)
        postgres_trace = self._postgres.read_trace(run_id)
        jsonl_events = list(jsonl_trace.get("events") or [])
        postgres_events = list(postgres_trace.get("events") or [])
        ordering_match = jsonl_events == postgres_events
        report = RunTraceParity(
            run_id=run_id,
            jsonl_event_count=len(jsonl_events),
            postgres_event_count=len(postgres_events),
            jsonl_checksum=_event_checksum(jsonl_events),
            postgres_checksum=_event_checksum(postgres_events),
            ordering_match=ordering_match,
            mismatch_reason="" if ordering_match else "event payload/order mismatch",
        )
        self._postgres.record_parity(report)
        return report

    @property
    def jsonl_repository(self) -> JsonlRunTraceRepository:
        return self._jsonl

    @property
    def postgres_repository(self) -> PostgresRunTraceRepository:
        return self._postgres

    def reset_all(self) -> None:
        self._postgres.reset_all()
        for path in self._jsonl.root_dir.glob("run-*.jsonl"):
            path.unlink(missing_ok=True)
        for path in self._jsonl.root_dir.glob("run-*.summary.json"):
            path.unlink(missing_ok=True)


__all__ = ["DualWriteRunTraceRepository"]
