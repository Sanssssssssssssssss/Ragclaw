from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from src.backend.observability.trace_store import RunTracePaths, _event_checksum, _trace_listing
from src.backend.observability.types import HarnessEvent, RunMetadata, RunOutcome
from src.backend.runtime.postgres_support import apply_postgres_migrations, postgres_connect


@dataclass(frozen=True)
class RunTraceParity:
    run_id: str
    jsonl_event_count: int
    postgres_event_count: int
    jsonl_checksum: str
    postgres_checksum: str
    ordering_match: bool
    mismatch_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "jsonl_event_count": self.jsonl_event_count,
            "postgres_event_count": self.postgres_event_count,
            "jsonl_checksum": self.jsonl_checksum,
            "postgres_checksum": self.postgres_checksum,
            "ordering_match": self.ordering_match,
            "mismatch_reason": self.mismatch_reason,
        }


class PostgresRunTraceRepository:
    def __init__(self, dsn: str, *, migrations_dir: Path) -> None:
        self._dsn = dsn
        self._migrations_dir = Path(migrations_dir)
        self._event_seq: dict[str, int] = {}
        self.ensure_schema()

    def _connect(self):
        return postgres_connect(self._dsn)

    def ensure_schema(self) -> None:
        apply_postgres_migrations(self._dsn, self._migrations_dir)

    def paths_for(self, run_id: str) -> RunTracePaths:
        return RunTracePaths(run_id=run_id, trace_path=Path("postgres"), summary_path=Path("postgres"))

    def create_run(self, metadata: RunMetadata) -> RunTracePaths:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO runs (
                    run_id, session_id, thread_id, user_message, source, started_at,
                    orchestration_engine, checkpoint_id, resume_source, run_status
                ) VALUES (
                    %(run_id)s, %(session_id)s, %(thread_id)s, %(user_message)s, %(source)s, %(started_at)s,
                    %(orchestration_engine)s, %(checkpoint_id)s, %(resume_source)s, %(run_status)s
                )
                """,
                metadata.to_dict(),
            )
        self._event_seq[metadata.run_id] = 0
        return self.paths_for(metadata.run_id)

    def append_event(self, event: HarnessEvent) -> None:
        seq = self._event_seq.get(event.run_id)
        if seq is None:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM run_events WHERE run_id = %s", (event.run_id,))
                row = cur.fetchone() or {"seq": 0}
                seq = int(row.get("seq", 0) or 0)
        seq += 1
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run_events (run_id, seq, event_id, name, ts, payload)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, event_id) DO NOTHING
                """,
                (
                    event.run_id,
                    seq,
                    event.event_id,
                    event.name,
                    event.ts,
                    Jsonb(event.payload),
                ),
            )
            inserted = cur.rowcount > 0
        if inserted:
            self._event_seq[event.run_id] = seq

    def finalize_run(self, run_id: str, outcome: RunOutcome) -> RunTracePaths:
        trace = self.read_trace(run_id)
        checksum = str(trace.get("event_checksum", "") or "")
        event_count = int(trace.get("event_count", 0) or 0)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE runs
                SET status = %(status)s,
                    final_answer = %(final_answer)s,
                    route_intent = %(route_intent)s,
                    used_skill = %(used_skill)s,
                    tool_names = %(tool_names)s,
                    retrieval_sources = %(retrieval_sources)s,
                    error_message = %(error_message)s,
                    completed_at = %(completed_at)s,
                    checkpoint_id = %(checkpoint_id)s,
                    resume_source = %(resume_source)s,
                    run_status = %(run_status)s,
                    orchestration_engine = %(orchestration_engine)s,
                    event_count = %(event_count)s,
                    event_checksum = %(event_checksum)s,
                    updated_at = NOW()
                WHERE run_id = %(run_id)s
                """,
                {
                    **outcome.to_dict(),
                    "run_id": run_id,
                    "tool_names": Jsonb(list(outcome.tool_names)),
                    "retrieval_sources": Jsonb(list(outcome.retrieval_sources)),
                    "event_count": event_count,
                    "event_checksum": checksum,
                },
            )
        return self.paths_for(run_id)

    def set_jsonl_paths(self, run_id: str, *, trace_path: Path, summary_path: Path) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE runs
                SET jsonl_trace_path = %s,
                    jsonl_summary_path = %s,
                    updated_at = NOW()
                WHERE run_id = %s
                """,
                (str(trace_path), str(summary_path), run_id),
            )

    def record_parity(self, report: RunTraceParity) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run_trace_parity (
                    run_id, jsonl_event_count, postgres_event_count,
                    jsonl_checksum, postgres_checksum, ordering_match, mismatch_reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    jsonl_event_count = EXCLUDED.jsonl_event_count,
                    postgres_event_count = EXCLUDED.postgres_event_count,
                    jsonl_checksum = EXCLUDED.jsonl_checksum,
                    postgres_checksum = EXCLUDED.postgres_checksum,
                    ordering_match = EXCLUDED.ordering_match,
                    mismatch_reason = EXCLUDED.mismatch_reason,
                    checked_at = NOW()
                """,
                (
                    report.run_id,
                    report.jsonl_event_count,
                    report.postgres_event_count,
                    report.jsonl_checksum,
                    report.postgres_checksum,
                    report.ordering_match,
                    report.mismatch_reason,
                ),
            )

    def read_trace(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM runs WHERE run_id = %s", (run_id,))
            run_row = cur.fetchone()
            if not run_row:
                raise FileNotFoundError(f"trace does not exist for run_id={run_id}")
            cur.execute(
                "SELECT seq, event_id, name, ts, payload FROM run_events WHERE run_id = %s ORDER BY seq ASC",
                (run_id,),
            )
            event_rows = cur.fetchall()

        metadata = {
            "run_id": run_row["run_id"],
            "session_id": run_row.get("session_id"),
            "thread_id": run_row.get("thread_id"),
            "user_message": run_row.get("user_message", ""),
            "source": run_row.get("source", "chat_api"),
            "started_at": run_row.get("started_at", ""),
            "orchestration_engine": run_row.get("orchestration_engine", ""),
            "checkpoint_id": run_row.get("checkpoint_id", ""),
            "resume_source": run_row.get("resume_source", ""),
            "run_status": run_row.get("run_status", "fresh"),
        }
        outcome = None
        if run_row.get("status"):
            outcome = {
                "status": run_row.get("status", ""),
                "final_answer": run_row.get("final_answer", ""),
                "route_intent": run_row.get("route_intent", ""),
                "used_skill": run_row.get("used_skill", ""),
                "tool_names": list(run_row.get("tool_names") or []),
                "retrieval_sources": list(run_row.get("retrieval_sources") or []),
                "error_message": run_row.get("error_message", ""),
                "completed_at": run_row.get("completed_at", ""),
                "thread_id": run_row.get("thread_id"),
                "orchestration_engine": run_row.get("orchestration_engine", ""),
                "checkpoint_id": run_row.get("checkpoint_id", ""),
                "resume_source": run_row.get("resume_source", ""),
                "run_status": run_row.get("run_status", "fresh"),
            }
        events = [
            {
                "event_id": row["event_id"],
                "run_id": run_id,
                "name": row["name"],
                "ts": row["ts"],
                "payload": dict(row.get("payload") or {}),
            }
            for row in event_rows
        ]
        return {
            "run_id": run_id,
            "metadata": metadata,
            "events": events,
            "outcome": outcome,
            "event_count": len(events),
            "event_checksum": _event_checksum(events),
            "summary_path": "",
            "trace_path": "",
            "jsonl_trace_path": str(run_row.get("jsonl_trace_path", "") or ""),
            "jsonl_summary_path": str(run_row.get("jsonl_summary_path", "") or ""),
        }

    def list_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id = %s")
            params.append(session_id)
        if status:
            clauses.append("status = %s")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT run_id
                FROM runs
                {where_sql}
                ORDER BY started_at DESC, created_at DESC
                LIMIT %s OFFSET %s
                """,
                params,
            )
            run_ids = [str(row["run_id"]) for row in cur.fetchall()]
        return [_trace_listing(self.read_trace(run_id)) for run_id in run_ids]

    def list_run_events(self, run_id: str, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT event_id, name, ts, payload
                FROM run_events
                WHERE run_id = %s
                ORDER BY seq ASC
                LIMIT %s OFFSET %s
                """,
                (run_id, limit, offset),
            )
            return [
                {
                    "event_id": row["event_id"],
                    "run_id": run_id,
                    "name": row["name"],
                    "ts": row["ts"],
                    "payload": dict(row.get("payload") or {}),
                }
                for row in cur.fetchall()
            ]

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total_runs FROM runs")
            total_runs = int((cur.fetchone() or {}).get("total_runs", 0) or 0)
            cur.execute(
                "SELECT status, COUNT(*) AS total FROM runs GROUP BY status ORDER BY status"
            )
            by_status = {str(row["status"] or "unknown"): int(row["total"] or 0) for row in cur.fetchall()}
            cur.execute(
                "SELECT source, COUNT(*) AS total FROM runs GROUP BY source ORDER BY source"
            )
            by_source = {str(row["source"] or "unknown"): int(row["total"] or 0) for row in cur.fetchall()}
        return {
            "total_runs": total_runs,
            "completed_runs": by_status.get("completed", 0),
            "failed_runs": by_status.get("failed", 0),
            "by_status": by_status,
            "by_source": by_source,
        }

    def parity_report(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM run_trace_parity WHERE run_id = %s", (run_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def reset_all(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE run_trace_parity, run_events, runs, hitl_decisions, hitl_requests, session_messages, sessions RESTART IDENTITY CASCADE")
        self._event_seq.clear()


__all__ = ["PostgresRunTraceRepository", "RunTraceParity"]
