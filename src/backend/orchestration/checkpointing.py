from __future__ import annotations

import asyncio
import atexit
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def _default_checkpoint_db_path() -> Path:
    project_root = Path(__file__).resolve().parents[3]
    return project_root / "backend" / "storage" / "langgraph" / "checkpoints.sqlite"


_CHECKPOINTER_ALLOWLIST = (
    ("src.backend.decision.lightweight_router", "RoutingDecision"),
    ("src.backend.decision.skill_gate", "SkillDecision"),
    ("src.backend.decision.execution_strategy", "ExecutionStrategy"),
    ("src.backend.knowledge.types", "Evidence"),
    ("src.backend.knowledge.types", "RetrievalStep"),
    ("src.backend.knowledge.types", "OrchestratedRetrievalResult"),
    ("src.backend.observability.types", "GuardResult"),
)


@dataclass(frozen=True)
class CheckpointSummary:
    checkpoint_id: str
    thread_id: str
    checkpoint_ns: str
    created_at: str
    source: str
    step: int
    run_id: str
    session_id: str | None
    user_message: str
    route_intent: str
    final_answer: str
    is_latest: bool
    state_label: str
    resume_eligible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "thread_id": self.thread_id,
            "checkpoint_ns": self.checkpoint_ns,
            "created_at": self.created_at,
            "source": self.source,
            "step": self.step,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "user_message": self.user_message,
            "route_intent": self.route_intent,
            "final_answer": self.final_answer,
            "is_latest": self.is_latest,
            "state_label": self.state_label,
            "resume_eligible": self.resume_eligible,
        }


@dataclass(frozen=True)
class PendingHitlRequest:
    request_id: str
    run_id: str
    thread_id: str
    session_id: str | None
    checkpoint_id: str
    capability_id: str
    capability_type: str
    display_name: str
    risk_level: str
    reason: str
    proposed_input: dict[str, Any]
    requested_at: str
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "session_id": self.session_id,
            "checkpoint_id": self.checkpoint_id,
            "capability_id": self.capability_id,
            "capability_type": self.capability_type,
            "display_name": self.display_name,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "proposed_input": dict(self.proposed_input),
            "requested_at": self.requested_at,
            "status": self.status,
        }


@dataclass(frozen=True)
class HitlDecisionRecord:
    decision_id: str
    request_id: str
    decision: str
    actor_id: str
    actor_type: str
    decided_at: str
    resume_source: str
    approved_input_snapshot: dict[str, Any] | None = None
    edited_input_snapshot: dict[str, Any] | None = None
    rejected_input_snapshot: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "request_id": self.request_id,
            "decision": self.decision,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "decided_at": self.decided_at,
            "resume_source": self.resume_source,
            "approved_input_snapshot": dict(self.approved_input_snapshot) if self.approved_input_snapshot is not None else None,
            "edited_input_snapshot": dict(self.edited_input_snapshot) if self.edited_input_snapshot is not None else None,
            "rejected_input_snapshot": dict(self.rejected_input_snapshot) if self.rejected_input_snapshot is not None else None,
        }


class AsyncCompatibleSqliteSaver(SqliteSaver):
    async def aget(self, config: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self.get, config)

    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int | None = None,
    ):
        items = await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        for item in items:
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def acopy_thread(self, thread_id: str, target_thread_id: str) -> None:
        await asyncio.to_thread(self.copy_thread, thread_id, target_thread_id)

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

    async def adelete_for_runs(self, *, thread_id: str | None = None, run_ids: list[str] | None = None) -> None:
        await asyncio.to_thread(self.delete_for_runs, thread_id=thread_id, run_ids=run_ids)

    async def aprune(self, *, thread_id: str, before: dict[str, Any] | None = None, limit: int | None = None) -> None:
        await asyncio.to_thread(self.prune, thread_id=thread_id, before=before, limit=limit)


class LangGraphCheckpointStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._db_path = Path()
        self._conn: sqlite3.Connection | None = None
        self._raw_saver: AsyncCompatibleSqliteSaver | None = None
        self._saver = None
        self.configure(db_path or _default_checkpoint_db_path())

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def saver(self):
        if self._saver is None:
            raise RuntimeError("checkpoint saver is not configured")
        return self._saver

    def configure_for_base_dir(self, base_dir: str | Path) -> None:
        root = Path(base_dir)
        self.configure(root / "storage" / "langgraph" / "checkpoints.sqlite")

    def configure(self, db_path: str | Path) -> None:
        resolved = Path(db_path).resolve()
        with self._lock:
            if self._conn is not None and resolved == self._db_path:
                return
            self.close()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(resolved), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            raw_saver = AsyncCompatibleSqliteSaver(
                conn,
                serde=JsonPlusSerializer(
                    allowed_msgpack_modules=_CHECKPOINTER_ALLOWLIST,
                ),
            )
            raw_saver.setup()
            self._db_path = resolved
            self._conn = conn
            self._raw_saver = raw_saver
            self._saver = raw_saver.with_allowlist(_CHECKPOINTER_ALLOWLIST)
            self._ensure_schema()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = None
            self._raw_saver = None
            self._saver = None

    def thread_id_for(self, *, session_id: str | None, run_id: str) -> str:
        return str(session_id or run_id)

    def list_thread_checkpoints(self, thread_id: str, *, limit: int | None = 50) -> list[CheckpointSummary]:
        with self._lock:
            config = {"configurable": {"thread_id": thread_id}}
            tuples = list(self.saver.list(config, limit=limit))
            latest_id = ""
            if tuples:
                latest_id = str(tuples[0].config.get("configurable", {}).get("checkpoint_id", "") or "")
            return [self._tuple_to_summary(item, latest_id=latest_id) for item in tuples]

    def get_checkpoint(self, *, thread_id: str, checkpoint_id: str) -> CheckpointSummary | None:
        for item in self.list_thread_checkpoints(thread_id):
            if item.checkpoint_id == checkpoint_id:
                return item
        return None

    def checkpoint_config(self, *, thread_id: str, checkpoint_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}}

    def latest_checkpoint(self, *, thread_id: str) -> CheckpointSummary | None:
        items = self.list_thread_checkpoints(thread_id, limit=1)
        return items[0] if items else None

    def record_pending_hitl(self, request: PendingHitlRequest) -> tuple[PendingHitlRequest, bool]:
        with self._lock:
            existing = self.get_hitl_request(thread_id=request.thread_id, checkpoint_id=request.checkpoint_id)
            if existing is not None:
                return existing, False
            stored = PendingHitlRequest(
                request_id=request.request_id or f"hitl-request-{uuid4().hex}",
                run_id=request.run_id,
                thread_id=request.thread_id,
                session_id=request.session_id,
                checkpoint_id=request.checkpoint_id,
                capability_id=request.capability_id,
                capability_type=request.capability_type,
                display_name=request.display_name,
                risk_level=request.risk_level,
                reason=request.reason,
                proposed_input=dict(request.proposed_input),
                requested_at=request.requested_at,
                status="pending",
            )
            conn = self._conn_or_raise()
            conn.execute(
                """
                INSERT INTO hitl_requests (
                    request_id,
                    run_id,
                    thread_id,
                    session_id,
                    checkpoint_id,
                    capability_id,
                    capability_type,
                    display_name,
                    risk_level,
                    reason,
                    proposed_input_json,
                    requested_at,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.request_id,
                    stored.run_id,
                    stored.thread_id,
                    stored.session_id,
                    stored.checkpoint_id,
                    stored.capability_id,
                    stored.capability_type,
                    stored.display_name,
                    stored.risk_level,
                    stored.reason,
                    json.dumps(stored.proposed_input, ensure_ascii=False),
                    stored.requested_at,
                    stored.status,
                ),
            )
            conn.commit()
            return stored, True

    def clear_pending_hitl(self, *, thread_id: str, checkpoint_id: str | None = None) -> None:
        with self._lock:
            conn = self._conn_or_raise()
            if checkpoint_id:
                conn.execute(
                    "DELETE FROM hitl_requests WHERE thread_id = ? AND checkpoint_id = ? AND status = 'pending'",
                    (thread_id, checkpoint_id),
                )
            else:
                conn.execute(
                    "DELETE FROM hitl_requests WHERE thread_id = ? AND status = 'pending'",
                    (thread_id,),
                )
            conn.commit()

    def pending_hitl(self, *, thread_id: str) -> PendingHitlRequest | None:
        with self._lock:
            row = self._conn_or_raise().execute(
                """
                SELECT *
                FROM hitl_requests
                WHERE thread_id = ? AND status = 'pending'
                ORDER BY requested_at DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
            return self._request_from_row(row)

    def list_pending_hitl(self, *, limit: int = 50) -> list[PendingHitlRequest]:
        with self._lock:
            rows = self._conn_or_raise().execute(
                """
                SELECT *
                FROM hitl_requests
                WHERE status = 'pending'
                ORDER BY requested_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [item for item in (self._request_from_row(row) for row in rows) if item is not None]

    def list_hitl_requests(self, *, thread_id: str, limit: int = 25) -> list[PendingHitlRequest]:
        with self._lock:
            rows = self._conn_or_raise().execute(
                """
                SELECT *
                FROM hitl_requests
                WHERE thread_id = ?
                ORDER BY requested_at DESC
                LIMIT ?
                """,
                (thread_id, max(1, int(limit))),
            ).fetchall()
            return [item for item in (self._request_from_row(row) for row in rows) if item is not None]

    def get_hitl_request(self, *, thread_id: str, checkpoint_id: str) -> PendingHitlRequest | None:
        with self._lock:
            row = self._conn_or_raise().execute(
                """
                SELECT *
                FROM hitl_requests
                WHERE thread_id = ? AND checkpoint_id = ?
                LIMIT 1
                """,
                (thread_id, checkpoint_id),
            ).fetchone()
            return self._request_from_row(row)

    def get_hitl_decision(
        self,
        *,
        request_id: str | None = None,
        thread_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> HitlDecisionRecord | None:
        with self._lock:
            conn = self._conn_or_raise()
            if request_id:
                row = conn.execute(
                    "SELECT * FROM hitl_decisions WHERE request_id = ? LIMIT 1",
                    (request_id,),
                ).fetchone()
                return self._decision_from_row(row)
            if thread_id and checkpoint_id:
                row = conn.execute(
                    """
                    SELECT decisions.*
                    FROM hitl_decisions AS decisions
                    INNER JOIN hitl_requests AS requests ON requests.request_id = decisions.request_id
                    WHERE requests.thread_id = ? AND requests.checkpoint_id = ?
                    LIMIT 1
                    """,
                    (thread_id, checkpoint_id),
                ).fetchone()
                return self._decision_from_row(row)
            return None

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
        edited_input_snapshot: dict[str, Any] | None = None,
    ) -> tuple[PendingHitlRequest | None, HitlDecisionRecord | None, bool]:
        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"approve", "reject", "edit"}:
            raise ValueError("decision must be approve, reject, or edit")
        with self._lock:
            request = self.get_hitl_request(thread_id=thread_id, checkpoint_id=checkpoint_id)
            if request is None:
                return None, None, False
            existing = self.get_hitl_decision(request_id=request.request_id)
            if existing is not None:
                return request, existing, False
            snapshot = dict(request.proposed_input)
            edited_snapshot = dict(edited_input_snapshot or {}) if normalized_decision == "edit" else None
            record = HitlDecisionRecord(
                decision_id=f"hitl-decision-{uuid4().hex}",
                request_id=request.request_id,
                decision=normalized_decision,
                actor_id=str(actor_id or "").strip() or "unknown",
                actor_type=str(actor_type or "").strip() or "unknown",
                decided_at=decided_at,
                resume_source=str(resume_source or "").strip(),
                approved_input_snapshot=snapshot if normalized_decision == "approve" else None,
                edited_input_snapshot=edited_snapshot,
                rejected_input_snapshot=snapshot if normalized_decision == "reject" else None,
            )
            conn = self._conn_or_raise()
            conn.execute(
                """
                INSERT INTO hitl_decisions (
                    decision_id,
                    request_id,
                    decision,
                    actor_id,
                    actor_type,
                    decided_at,
                    resume_source,
                    approved_input_snapshot_json,
                    edited_input_snapshot_json,
                    rejected_input_snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.decision_id,
                    record.request_id,
                    record.decision,
                    record.actor_id,
                    record.actor_type,
                    record.decided_at,
                    record.resume_source,
                    json.dumps(record.approved_input_snapshot, ensure_ascii=False)
                    if record.approved_input_snapshot is not None
                    else None,
                    json.dumps(record.edited_input_snapshot, ensure_ascii=False)
                    if record.edited_input_snapshot is not None
                    else None,
                    json.dumps(record.rejected_input_snapshot, ensure_ascii=False)
                    if record.rejected_input_snapshot is not None
                    else None,
                ),
            )
            conn.execute(
                "UPDATE hitl_requests SET status = ? WHERE request_id = ?",
                (record.decision, request.request_id),
            )
            conn.commit()
            return self.get_hitl_request(thread_id=thread_id, checkpoint_id=checkpoint_id), record, True

    def _tuple_to_summary(self, item, *, latest_id: str) -> CheckpointSummary:
        config = dict(getattr(item, "config", {}) or {})
        configurable = dict(config.get("configurable", {}) or {})
        checkpoint = dict(getattr(item, "checkpoint", {}) or {})
        metadata = dict(getattr(item, "metadata", {}) or {})
        channel_values = dict(checkpoint.get("channel_values", {}) or {})

        checkpoint_id = str(configurable.get("checkpoint_id", "") or checkpoint.get("id", "") or "")
        thread_id = str(configurable.get("thread_id", "") or "")
        is_latest = checkpoint_id == latest_id
        source = str(metadata.get("source", "") or "")
        step = int(metadata.get("step", -1) or -1)
        final_answer = str(channel_values.get("final_answer", "") or "")
        pending = self.pending_hitl(thread_id=thread_id)
        decision = self.get_hitl_decision(thread_id=thread_id, checkpoint_id=checkpoint_id)
        has_pending_hitl = pending is not None and pending.checkpoint_id == checkpoint_id
        has_decision = decision is not None
        if has_pending_hitl:
            state_label = "interrupted"
        elif has_decision and not final_answer.strip():
            state_label = "restoring"
        elif final_answer.strip():
            state_label = "completed"
        elif is_latest:
            state_label = "fresh"
        else:
            state_label = "interrupted"
        resume_eligible = bool(step >= 0 and not final_answer.strip())
        return CheckpointSummary(
            checkpoint_id=checkpoint_id,
            thread_id=thread_id,
            checkpoint_ns=str(configurable.get("checkpoint_ns", "") or ""),
            created_at=str(checkpoint.get("ts", "") or ""),
            source=source,
            step=step,
            run_id=str(channel_values.get("run_id", "") or ""),
            session_id=str(channel_values.get("session_id")) if channel_values.get("session_id") is not None else None,
            user_message=str(channel_values.get("user_message", "") or ""),
            route_intent=self._extract_route_intent(channel_values),
            final_answer=final_answer,
            is_latest=is_latest,
            state_label=state_label,
            resume_eligible=resume_eligible,
        )

    def _ensure_schema(self) -> None:
        conn = self._conn_or_raise()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS hitl_requests (
                request_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                session_id TEXT,
                checkpoint_id TEXT NOT NULL UNIQUE,
                capability_id TEXT NOT NULL,
                capability_type TEXT NOT NULL,
                display_name TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                reason TEXT NOT NULL,
                proposed_input_json TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_hitl_requests_thread_status
                ON hitl_requests(thread_id, status, requested_at DESC);

            CREATE TABLE IF NOT EXISTS hitl_decisions (
                decision_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                decision TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                decided_at TEXT NOT NULL,
                resume_source TEXT NOT NULL,
                approved_input_snapshot_json TEXT,
                edited_input_snapshot_json TEXT,
                rejected_input_snapshot_json TEXT,
                FOREIGN KEY (request_id) REFERENCES hitl_requests(request_id)
            );
            """
        )
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(hitl_decisions)").fetchall()
        }
        if "edited_input_snapshot_json" not in columns:
            conn.execute("ALTER TABLE hitl_decisions ADD COLUMN edited_input_snapshot_json TEXT")
        conn.commit()

    def _extract_route_intent(self, channel_values: dict[str, Any]) -> str:
        route_decision = channel_values.get("route_decision")
        if isinstance(route_decision, dict):
            return str(route_decision.get("intent", "") or "")
        return str(getattr(route_decision, "intent", "") or "")

    def _request_from_row(self, row: sqlite3.Row | None) -> PendingHitlRequest | None:
        if row is None:
            return None
        return PendingHitlRequest(
            request_id=str(row["request_id"]),
            run_id=str(row["run_id"]),
            thread_id=str(row["thread_id"]),
            session_id=str(row["session_id"]) if row["session_id"] is not None else None,
            checkpoint_id=str(row["checkpoint_id"]),
            capability_id=str(row["capability_id"]),
            capability_type=str(row["capability_type"]),
            display_name=str(row["display_name"]),
            risk_level=str(row["risk_level"]),
            reason=str(row["reason"]),
            proposed_input=dict(json.loads(str(row["proposed_input_json"]) or "{}")),
            requested_at=str(row["requested_at"]),
            status=str(row["status"]),
        )

    def _decision_from_row(self, row: sqlite3.Row | None) -> HitlDecisionRecord | None:
        if row is None:
            return None
        approved_snapshot = row["approved_input_snapshot_json"]
        edited_snapshot = row["edited_input_snapshot_json"] if "edited_input_snapshot_json" in row.keys() else None
        rejected_snapshot = row["rejected_input_snapshot_json"]
        return HitlDecisionRecord(
            decision_id=str(row["decision_id"]),
            request_id=str(row["request_id"]),
            decision=str(row["decision"]),
            actor_id=str(row["actor_id"]),
            actor_type=str(row["actor_type"]),
            decided_at=str(row["decided_at"]),
            resume_source=str(row["resume_source"]),
            approved_input_snapshot=dict(json.loads(str(approved_snapshot))) if approved_snapshot else None,
            edited_input_snapshot=dict(json.loads(str(edited_snapshot))) if edited_snapshot else None,
            rejected_input_snapshot=dict(json.loads(str(rejected_snapshot))) if rejected_snapshot else None,
        )

    def _conn_or_raise(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("checkpoint store is not configured")
        return self._conn


checkpoint_store = LangGraphCheckpointStore()
atexit.register(checkpoint_store.close)
