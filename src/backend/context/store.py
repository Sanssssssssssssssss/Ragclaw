from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from src.backend.context.manifest import score_manifest
from src.backend.context.models import (
    ConsolidationRunSummary,
    ContextAssembly,
    ContextAssemblyDecision,
    ContextAuditRecord,
    ContextEnvelope,
    ContextModelCallSnapshot,
    ContextTurnSnapshot,
    ConversationRecallRecord,
    EpisodicSummary,
    MemoryCandidate,
    MemoryKind,
    MemoryManifest,
    StoredMemory,
    WorkingMemory,
)
from src.backend.context.policies import freshness_state


@dataclass(frozen=True)
class ThreadContextSnapshot:
    thread_id: str
    session_id: str | None
    run_id: str
    working_memory: dict[str, Any]
    episodic_summary: dict[str, Any]
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "working_memory": dict(self.working_memory),
            "episodic_summary": dict(self.episodic_summary),
            "updated_at": self.updated_at,
        }


class ContextStore:
    def __init__(self) -> None:
        self._base_dir: Path | None = None
        self._db_path: Path | None = None
        self._conn: sqlite3.Connection | None = None
        self._lock = RLock()

    def configure_for_base_dir(self, base_dir: Path) -> None:
        db_path = Path(base_dir) / "storage" / "context" / "context.sqlite"
        with self._lock:
            if self._db_path == db_path and self._conn is not None:
                return
            if self._conn is not None:
                self._conn.close()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._conn = conn
            self._db_path = db_path
            self._base_dir = Path(base_dir)
            self._ensure_schema(conn)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = None
            self._db_path = None
            self._base_dir = None

    def _conn_or_raise(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Context store is not configured")
        return self._conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS thread_context (
                thread_id TEXT PRIMARY KEY,
                session_id TEXT,
                run_id TEXT NOT NULL,
                working_memory_json TEXT NOT NULL,
                episodic_summary_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                namespace TEXT NOT NULL,
                memory_type TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL DEFAULT 'project',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                freshness TEXT NOT NULL DEFAULT 'fresh',
                stale_after TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                supersedes_json TEXT NOT NULL DEFAULT '[]',
                applicability_json TEXT NOT NULL DEFAULT '{}',
                direct_prompt INTEGER NOT NULL DEFAULT 0,
                promotion_priority INTEGER NOT NULL DEFAULT 0,
                conflict_flag INTEGER NOT NULL DEFAULT 0,
                conflict_with_json TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 1,
                fingerprint TEXT NOT NULL UNIQUE,
                conflict_key TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS context_assemblies (
                assembly_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                call_site TEXT NOT NULL,
                path_kind TEXT NOT NULL,
                assembly_json TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS context_turns (
                turn_id TEXT PRIMARY KEY,
                session_id TEXT,
                run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                assistant_message_id TEXT,
                segment_index INTEGER NOT NULL,
                call_site TEXT NOT NULL,
                path_type TEXT NOT NULL,
                user_query TEXT NOT NULL,
                context_envelope_json TEXT NOT NULL,
                assembly_decision_json TEXT NOT NULL,
                budget_report_json TEXT NOT NULL,
                selected_memory_ids_json TEXT NOT NULL,
                selected_artifact_ids_json TEXT NOT NULL,
                selected_evidence_ids_json TEXT NOT NULL,
                selected_conversation_ids_json TEXT NOT NULL,
                dropped_items_json TEXT NOT NULL,
                truncation_reason TEXT NOT NULL,
                run_status TEXT NOT NULL DEFAULT 'fresh',
                resume_source TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL DEFAULT '',
                orchestration_engine TEXT NOT NULL DEFAULT 'langgraph',
                model_invoked INTEGER NOT NULL DEFAULT 1,
                excluded_from_context INTEGER NOT NULL DEFAULT 0,
                excluded_at TEXT NOT NULL DEFAULT '',
                exclusion_reason TEXT NOT NULL DEFAULT '',
                call_ids_json TEXT NOT NULL DEFAULT '[]',
                post_state_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS context_model_calls (
                call_id TEXT PRIMARY KEY,
                session_id TEXT,
                run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                call_type TEXT NOT NULL,
                call_site TEXT NOT NULL,
                path_type TEXT NOT NULL,
                user_query TEXT NOT NULL,
                context_envelope_json TEXT NOT NULL,
                assembly_decision_json TEXT NOT NULL,
                budget_report_json TEXT NOT NULL,
                selected_memory_ids_json TEXT NOT NULL,
                selected_artifact_ids_json TEXT NOT NULL,
                selected_evidence_ids_json TEXT NOT NULL,
                selected_conversation_ids_json TEXT NOT NULL,
                dropped_items_json TEXT NOT NULL,
                truncation_reason TEXT NOT NULL,
                run_status TEXT NOT NULL DEFAULT 'fresh',
                resume_source TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL DEFAULT '',
                orchestration_engine TEXT NOT NULL DEFAULT 'langgraph',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversation_recall (
                chunk_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                session_id TEXT,
                run_id TEXT NOT NULL,
                role TEXT NOT NULL,
                source_message_id TEXT NOT NULL,
                snippet TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                source_turn_ids_json TEXT NOT NULL DEFAULT '[]',
                source_run_ids_json TEXT NOT NULL DEFAULT '[]',
                source_memory_ids_json TEXT NOT NULL DEFAULT '[]',
                generated_by TEXT NOT NULL DEFAULT '',
                generated_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS context_audit (
                audit_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                session_id TEXT,
                thread_id TEXT NOT NULL,
                run_id TEXT NOT NULL DEFAULT '',
                turn_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS consolidation_runs (
                consolidation_id TEXT PRIMARY KEY,
                trigger TEXT NOT NULL,
                thread_id TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                summary_json TEXT NOT NULL
            );
            """
        )
        self._ensure_memory_columns(conn)
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_memories_kind_namespace ON memories(kind, namespace, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_memories_conflict_key ON memories(conflict_key);
            CREATE INDEX IF NOT EXISTS idx_context_turns_session_created ON context_turns(session_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_context_turns_thread_created ON context_turns(thread_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_context_turns_run_segment ON context_turns(run_id, segment_index DESC);
            CREATE INDEX IF NOT EXISTS idx_context_model_calls_turn_created ON context_model_calls(turn_id, created_at ASC);
            CREATE INDEX IF NOT EXISTS idx_context_model_calls_session_created ON context_model_calls(session_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_conversation_recall_thread ON conversation_recall(thread_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_context_audit_thread_created ON context_audit(thread_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_consolidation_runs_created ON consolidation_runs(created_at DESC);
            """
        )
        conn.commit()

    def _ensure_memory_columns(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(memories)").fetchall()
        columns = {str(row["name"]): row for row in rows}
        desired: dict[str, str] = {
            "memory_type": "TEXT NOT NULL DEFAULT ''",
            "scope": "TEXT NOT NULL DEFAULT 'project'",
            "confidence": "REAL NOT NULL DEFAULT 0.5",
            "freshness": "TEXT NOT NULL DEFAULT 'fresh'",
            "stale_after": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "supersedes_json": "TEXT NOT NULL DEFAULT '[]'",
            "applicability_json": "TEXT NOT NULL DEFAULT '{}'",
            "direct_prompt": "INTEGER NOT NULL DEFAULT 0",
            "promotion_priority": "INTEGER NOT NULL DEFAULT 0",
            "conflict_flag": "INTEGER NOT NULL DEFAULT 0",
            "conflict_with_json": "TEXT NOT NULL DEFAULT '[]'",
            "conflict_key": "TEXT NOT NULL DEFAULT ''",
            "source_turn_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "source_run_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "source_memory_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "generated_by": "TEXT NOT NULL DEFAULT ''",
            "generated_at": "TEXT NOT NULL DEFAULT ''",
        }
        for column_name, ddl in desired.items():
            if column_name not in columns:
                conn.execute(f"ALTER TABLE memories ADD COLUMN {column_name} {ddl}")

        conn.execute(
            """
            UPDATE memories
            SET memory_type = CASE
                WHEN memory_type != '' THEN memory_type
                WHEN kind = 'procedural' THEN 'workflow_rule'
                WHEN kind = 'episodic' THEN 'session_episode'
                ELSE 'project_fact'
            END
            """
        )
        conn.execute(
            """
            UPDATE memories
            SET scope = CASE
                WHEN scope != '' THEN scope
                WHEN namespace LIKE 'user:%' THEN 'user'
                WHEN namespace LIKE 'thread:%' THEN 'thread'
                ELSE 'project'
            END
            """
        )
        conn.execute(
            """
            UPDATE memories
            SET conflict_key = CASE
                WHEN conflict_key != '' THEN conflict_key
                ELSE lower(memory_type || '|' || namespace || '|' || replace(title, ' ', '-'))
            END
            """
        )

    def memory_index_path(self) -> Path:
        if self._base_dir is None:
            raise RuntimeError("Context store is not configured")
        root_dir = self._base_dir.parent if self._base_dir.name.lower() == "backend" else self._base_dir
        return root_dir / "memory" / "MEMORY.md"

    def write_memory_index(self, content: str) -> Path:
        path = self.memory_index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def upsert_thread_snapshot(
        self,
        *,
        thread_id: str,
        session_id: str | None,
        run_id: str,
        working_memory: WorkingMemory | dict[str, Any],
        episodic_summary: EpisodicSummary | dict[str, Any],
        updated_at: str,
    ) -> None:
        working_payload = working_memory.to_dict() if hasattr(working_memory, "to_dict") else dict(working_memory)
        episodic_payload = episodic_summary.to_dict() if hasattr(episodic_summary, "to_dict") else dict(episodic_summary)
        with self._lock:
            self._conn_or_raise().execute(
                """
                INSERT INTO thread_context (
                    thread_id, session_id, run_id, working_memory_json, episodic_summary_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    run_id = excluded.run_id,
                    working_memory_json = excluded.working_memory_json,
                    episodic_summary_json = excluded.episodic_summary_json,
                    updated_at = excluded.updated_at
                """,
                (
                    thread_id,
                    session_id,
                    run_id,
                    json.dumps(working_payload, ensure_ascii=False),
                    json.dumps(episodic_payload, ensure_ascii=False),
                    updated_at,
                ),
            )
            self._conn_or_raise().commit()

    def get_thread_snapshot(self, *, thread_id: str) -> ThreadContextSnapshot | None:
        with self._lock:
            row = self._conn_or_raise().execute(
                "SELECT * FROM thread_context WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        if row is None:
            return None
        return ThreadContextSnapshot(
            thread_id=str(row["thread_id"] or ""),
            session_id=str(row["session_id"]) if row["session_id"] is not None else None,
            run_id=str(row["run_id"] or ""),
            working_memory=json.loads(str(row["working_memory_json"] or "{}")),
            episodic_summary=json.loads(str(row["episodic_summary_json"] or "{}")),
            updated_at=str(row["updated_at"] or ""),
        )

    def insert_memory(
        self,
        *,
        kind: MemoryKind,
        namespace: str,
        title: str,
        content: str,
        summary: str = "",
        tags: list[str] | tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
        source: str = "",
        created_at: str,
        fingerprint: str,
        memory_type: str | None = None,
        scope: str | None = None,
        confidence: float = 0.6,
        stale_after: str = "",
        status: str = "active",
        supersedes: list[str] | tuple[str, ...] = (),
        applicability: dict[str, Any] | None = None,
        direct_prompt: bool = False,
        promotion_priority: int = 0,
        conflict_key: str = "",
        source_turn_ids: list[str] | tuple[str, ...] = (),
        source_run_ids: list[str] | tuple[str, ...] = (),
        source_memory_ids: list[str] | tuple[str, ...] = (),
        generated_by: str = "",
        generated_at: str = "",
    ) -> StoredMemory:
        candidate = MemoryCandidate(
            kind=kind,
            memory_type=self._default_memory_type(kind, memory_type),
            scope=self._default_scope(namespace, scope),
            namespace=namespace,
            title=title,
            content=content,
            summary=summary,
            tags=tuple(tags),
            metadata=dict(metadata or {}),
            source=source,
            created_at=created_at,
            updated_at=created_at,
            confidence=float(confidence),
            stale_after=stale_after,
            status=status,  # type: ignore[arg-type]
            supersedes=tuple(str(item) for item in supersedes),
            applicability=dict(applicability or {}),
            direct_prompt=bool(direct_prompt),
            promotion_priority=int(promotion_priority),
            source_turn_ids=tuple(str(item) for item in source_turn_ids),
            source_run_ids=tuple(str(item) for item in source_run_ids),
            source_memory_ids=tuple(str(item) for item in source_memory_ids),
            generated_by=generated_by,
            generated_at=generated_at,
            fingerprint=fingerprint,
            conflict_key=conflict_key,
        )
        return self.insert_memory_candidate(candidate)

    def insert_memory_candidate(self, candidate: MemoryCandidate) -> StoredMemory:
        with self._lock:
            conn = self._conn_or_raise()
            self._refresh_staleness_locked(conn)
            existing = conn.execute(
                "SELECT * FROM memories WHERE fingerprint = ?",
                (candidate.fingerprint,),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    """
                    UPDATE memories
                    SET updated_at = ?, confidence = ?, metadata_json = ?, summary = ?, content = ?, status = ?, enabled = 1,
                        source_turn_ids_json = ?, source_run_ids_json = ?, source_memory_ids_json = ?, generated_by = ?, generated_at = ?
                    WHERE fingerprint = ?
                    """,
                    (
                        candidate.updated_at or candidate.created_at,
                        candidate.confidence,
                        json.dumps(dict(candidate.metadata), ensure_ascii=False),
                        candidate.summary,
                        candidate.content,
                        candidate.status,
                        json.dumps(list(candidate.source_turn_ids), ensure_ascii=False),
                        json.dumps(list(candidate.source_run_ids), ensure_ascii=False),
                        json.dumps(list(candidate.source_memory_ids), ensure_ascii=False),
                        candidate.generated_by,
                        candidate.generated_at,
                        candidate.fingerprint,
                    ),
                )
                conn.commit()
                refreshed = conn.execute("SELECT * FROM memories WHERE fingerprint = ?", (candidate.fingerprint,)).fetchone()
                return self._memory_from_row(refreshed)

            memory_id = f"mem-{uuid4().hex}"
            supersedes_ids: list[str] = list(candidate.supersedes)
            conflict_with_ids: list[str] = []
            conflict_flag = False

            if candidate.conflict_key:
                peers = conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE conflict_key = ? AND enabled = 1 AND status != 'dropped'
                    ORDER BY updated_at DESC
                    """,
                    (candidate.conflict_key,),
                ).fetchall()
                for peer in peers:
                    peer_record = self._memory_from_row(peer)
                    if peer_record.fingerprint == candidate.fingerprint:
                        continue
                    conflict_with_ids.append(peer_record.memory_id)
                    conflict_flag = True
                    should_supersede = (
                        candidate.confidence >= peer_record.confidence
                        and (candidate.updated_at or candidate.created_at) >= peer_record.updated_at
                    )
                    if should_supersede and peer_record.status != "superseded":
                        supersedes_ids.append(peer_record.memory_id)
                        peer_conflicts = tuple(dict.fromkeys((*peer_record.conflict_with, memory_id)))
                        conn.execute(
                            """
                            UPDATE memories
                            SET status = 'superseded', conflict_flag = 1, conflict_with_json = ?, updated_at = ?
                            WHERE memory_id = ?
                            """,
                            (
                                json.dumps(list(peer_conflicts), ensure_ascii=False),
                                candidate.updated_at or candidate.created_at,
                                peer_record.memory_id,
                            ),
                        )
                    elif peer_record.status != "superseded":
                        peer_conflicts = tuple(dict.fromkeys((*peer_record.conflict_with, memory_id)))
                        conn.execute(
                            """
                            UPDATE memories
                            SET conflict_flag = 1, conflict_with_json = ?, updated_at = ?
                            WHERE memory_id = ?
                            """,
                            (
                                json.dumps(list(peer_conflicts), ensure_ascii=False),
                                candidate.updated_at or candidate.created_at,
                                peer_record.memory_id,
                            ),
                        )

            freshness = freshness_state(candidate.updated_at or candidate.created_at, candidate.stale_after)
            effective_status = "stale" if candidate.status == "active" and freshness == "stale" else candidate.status

            conn.execute(
                """
                INSERT INTO memories (
                    memory_id, kind, namespace, memory_type, scope, title, content, summary, tags_json,
                    metadata_json, source, created_at, updated_at, confidence, freshness, stale_after, status,
                    supersedes_json, applicability_json, direct_prompt, promotion_priority, conflict_flag,
                    conflict_with_json, enabled, fingerprint, conflict_key, source_turn_ids_json,
                    source_run_ids_json, source_memory_ids_json, generated_by, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    candidate.kind,
                    candidate.namespace,
                    candidate.memory_type,
                    candidate.scope,
                    candidate.title,
                    candidate.content,
                    candidate.summary,
                    json.dumps(list(candidate.tags), ensure_ascii=False),
                    json.dumps(dict(candidate.metadata), ensure_ascii=False),
                    candidate.source,
                    candidate.created_at or candidate.updated_at,
                    candidate.updated_at or candidate.created_at,
                    candidate.confidence,
                    freshness,
                    candidate.stale_after,
                    effective_status,
                    json.dumps(list(dict.fromkeys(supersedes_ids)), ensure_ascii=False),
                    json.dumps(dict(candidate.applicability), ensure_ascii=False),
                    1 if candidate.direct_prompt else 0,
                    candidate.promotion_priority,
                    1 if conflict_flag else 0,
                    json.dumps(list(dict.fromkeys(conflict_with_ids)), ensure_ascii=False),
                    candidate.fingerprint,
                    candidate.conflict_key,
                    json.dumps(list(candidate.source_turn_ids), ensure_ascii=False),
                    json.dumps(list(candidate.source_run_ids), ensure_ascii=False),
                    json.dumps(list(candidate.source_memory_ids), ensure_ascii=False),
                    candidate.generated_by,
                    candidate.generated_at,
                ),
            )
            conn.commit()
        return self.get_memory(memory_id=memory_id)  # type: ignore[return-value]

    def get_memory(self, *, memory_id: str) -> StoredMemory | None:
        with self._lock:
            self._refresh_staleness_locked(self._conn_or_raise())
            row = self._conn_or_raise().execute(
                "SELECT * FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        return self._memory_from_row(row) if row is not None else None

    def get_memory_by_fingerprint(self, *, fingerprint: str) -> StoredMemory | None:
        with self._lock:
            self._refresh_staleness_locked(self._conn_or_raise())
            row = self._conn_or_raise().execute(
                "SELECT * FROM memories WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        return self._memory_from_row(row) if row is not None else None

    def update_memory(
        self,
        *,
        memory_id: str,
        title: str | None = None,
        content: str | None = None,
        summary: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
        updated_at: str,
        confidence: float | None = None,
        stale_after: str | None = None,
        status: str | None = None,
        supersedes: list[str] | tuple[str, ...] | None = None,
        conflict_flag: bool | None = None,
        conflict_with: list[str] | tuple[str, ...] | None = None,
        source_turn_ids: list[str] | tuple[str, ...] | None = None,
        source_run_ids: list[str] | tuple[str, ...] | None = None,
        source_memory_ids: list[str] | tuple[str, ...] | None = None,
        generated_by: str | None = None,
        generated_at: str | None = None,
    ) -> StoredMemory | None:
        current = self.get_memory(memory_id=memory_id)
        if current is None:
            return None
        next_stale_after = current.stale_after if stale_after is None else stale_after
        next_status = current.status if status is None else status
        next_freshness = freshness_state(updated_at, next_stale_after)
        if next_status == "active" and next_freshness == "stale":
            next_status = "stale"
        with self._lock:
            self._conn_or_raise().execute(
                """
                UPDATE memories
                SET title = ?, content = ?, summary = ?, tags_json = ?, metadata_json = ?, updated_at = ?,
                    confidence = ?, stale_after = ?, freshness = ?, status = ?, supersedes_json = ?,
                    conflict_flag = ?, conflict_with_json = ?, source_turn_ids_json = ?, source_run_ids_json = ?,
                    source_memory_ids_json = ?, generated_by = ?, generated_at = ?,
                    enabled = CASE WHEN ? = 'dropped' THEN 0 ELSE enabled END
                WHERE memory_id = ?
                """,
                (
                    str(title or current.title),
                    str(content or current.content),
                    str(summary if summary is not None else current.summary),
                    json.dumps(list(tags if tags is not None else current.tags), ensure_ascii=False),
                    json.dumps(dict(metadata if metadata is not None else current.metadata), ensure_ascii=False),
                    updated_at,
                    float(current.confidence if confidence is None else confidence),
                    next_stale_after,
                    next_freshness,
                    next_status,
                    json.dumps(list(supersedes if supersedes is not None else current.supersedes), ensure_ascii=False),
                    1 if (current.conflict_flag if conflict_flag is None else conflict_flag) else 0,
                    json.dumps(list(conflict_with if conflict_with is not None else current.conflict_with), ensure_ascii=False),
                    json.dumps(list(source_turn_ids if source_turn_ids is not None else current.source_turn_ids), ensure_ascii=False),
                    json.dumps(list(source_run_ids if source_run_ids is not None else current.source_run_ids), ensure_ascii=False),
                    json.dumps(list(source_memory_ids if source_memory_ids is not None else current.source_memory_ids), ensure_ascii=False),
                    str(generated_by if generated_by is not None else current.generated_by),
                    str(generated_at if generated_at is not None else current.generated_at),
                    next_status,
                    memory_id,
                ),
            )
            self._conn_or_raise().commit()
        return self.get_memory(memory_id=memory_id)

    def update_memory_status(
        self,
        *,
        memory_id: str,
        status: str,
        updated_at: str,
        conflict_flag: bool | None = None,
        conflict_with: list[str] | tuple[str, ...] | None = None,
    ) -> StoredMemory | None:
        current = self.get_memory(memory_id=memory_id)
        if current is None:
            return None
        return self.update_memory(
            memory_id=memory_id,
            updated_at=updated_at,
            status=status,
            conflict_flag=current.conflict_flag if conflict_flag is None else conflict_flag,
            conflict_with=current.conflict_with if conflict_with is None else conflict_with,
        )

    def disable_memory(self, *, memory_id: str) -> bool:
        with self._lock:
            cursor = self._conn_or_raise().execute(
                "UPDATE memories SET enabled = 0 WHERE memory_id = ?",
                (memory_id,),
            )
            self._conn_or_raise().commit()
        return bool(cursor.rowcount)

    def delete_memory(self, *, memory_id: str) -> bool:
        with self._lock:
            cursor = self._conn_or_raise().execute(
                "DELETE FROM memories WHERE memory_id = ?",
                (memory_id,),
            )
            self._conn_or_raise().commit()
        return bool(cursor.rowcount)

    def list_memories(
        self,
        *,
        kind: MemoryKind | None = None,
        namespace: str | None = None,
        limit: int = 20,
        include_inactive: bool = False,
    ) -> list[StoredMemory]:
        with self._lock:
            conn = self._conn_or_raise()
            self._refresh_staleness_locked(conn)
            query = "SELECT * FROM memories WHERE 1 = 1"
            params: list[Any] = []
            if not include_inactive:
                query += " AND enabled = 1 AND status NOT IN ('dropped', 'invalidated')"
            if kind is not None:
                query += " AND kind = ?"
                params.append(kind)
            if namespace is not None:
                query += " AND namespace = ?"
                params.append(namespace)
            query += " ORDER BY updated_at DESC LIMIT ?"
            params.append(max(1, int(limit)))
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def list_memory_manifests(
        self,
        *,
        kind: MemoryKind | None = None,
        namespace: str | None = None,
        limit: int = 20,
        status: str | None = None,
        include_dropped: bool = False,
    ) -> list[MemoryManifest]:
        with self._lock:
            conn = self._conn_or_raise()
            self._refresh_staleness_locked(conn)
            query = "SELECT * FROM memories WHERE enabled = 1"
            params: list[Any] = []
            if not include_dropped:
                query += " AND status NOT IN ('dropped', 'invalidated')"
            if kind is not None:
                query += " AND kind = ?"
                params.append(kind)
            if namespace is not None:
                query += " AND namespace = ?"
                params.append(namespace)
            if status is not None:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY updated_at DESC LIMIT ?"
            params.append(max(1, int(limit)))
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._memory_from_row(row).to_manifest() for row in rows]

    def search_memories(
        self,
        *,
        kind: MemoryKind,
        namespaces: list[str] | tuple[str, ...],
        query: str,
        limit: int = 5,
    ) -> list[StoredMemory]:
        manifests = self.search_memory_manifests(kind=kind, namespaces=namespaces, query=query, limit=limit)
        hydrated: list[StoredMemory] = []
        for manifest in manifests:
            record = self.get_memory(memory_id=manifest.memory_id)
            if record is not None:
                hydrated.append(record)
        return hydrated

    def search_memory_manifests(
        self,
        *,
        kind: MemoryKind | None = None,
        namespaces: list[str] | tuple[str, ...] = (),
        query: str,
        limit: int = 8,
    ) -> list[MemoryManifest]:
        normalized = str(query or "").strip()
        if not normalized:
            return []
        namespace_values = [str(item).strip() for item in namespaces if str(item).strip()]
        with self._lock:
            conn = self._conn_or_raise()
            self._refresh_staleness_locked(conn)
            sql = "SELECT * FROM memories WHERE enabled = 1 AND status NOT IN ('dropped', 'invalidated')"
            params: list[Any] = []
            if kind is not None:
                sql += " AND kind = ?"
                params.append(kind)
            if namespace_values:
                placeholders = ", ".join("?" for _ in namespace_values)
                sql += f" AND namespace IN ({placeholders})"
                params.extend(namespace_values)
            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(max(20, int(limit) * 8))
            rows = conn.execute(sql, tuple(params)).fetchall()

        scored: list[tuple[float, MemoryManifest]] = []
        for row in rows:
            manifest = self._memory_from_row(row).to_manifest()
            score = score_manifest(manifest, query=normalized)
            if score > 0:
                scored.append((score, manifest))
        scored.sort(key=lambda item: (-item[0], item[1].updated_at))
        return [manifest for _, manifest in scored[: max(1, int(limit))]]

    def insert_conversation_chunk(
        self,
        *,
        thread_id: str,
        session_id: str | None,
        run_id: str,
        role: str,
        source_message_id: str,
        snippet: str,
        summary: str,
        tags: list[str] | tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
        source_turn_ids: list[str] | tuple[str, ...] = (),
        source_run_ids: list[str] | tuple[str, ...] = (),
        source_memory_ids: list[str] | tuple[str, ...] = (),
        generated_by: str = "",
        generated_at: str = "",
        status: str = "active",
        created_at: str,
    ) -> ConversationRecallRecord:
        fingerprint = f"{thread_id}|{source_message_id}|{snippet.strip()}".strip()
        with self._lock:
            conn = self._conn_or_raise()
            row = conn.execute(
                "SELECT * FROM conversation_recall WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if row is not None:
                return self._conversation_from_row(row)
            chunk_id = f"conv-{uuid4().hex}"
            conn.execute(
                """
                INSERT INTO conversation_recall (
                    chunk_id, thread_id, session_id, run_id, role, source_message_id, snippet, summary, tags_json,
                    metadata_json, source_turn_ids_json, source_run_ids_json, source_memory_ids_json,
                    generated_by, generated_at, status, created_at, updated_at, fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    thread_id,
                    session_id,
                    run_id,
                    role,
                    source_message_id,
                    snippet,
                    summary,
                    json.dumps(list(tags), ensure_ascii=False),
                    json.dumps(dict(metadata or {}), ensure_ascii=False),
                    json.dumps(list(source_turn_ids), ensure_ascii=False),
                    json.dumps(list(source_run_ids), ensure_ascii=False),
                    json.dumps(list(source_memory_ids), ensure_ascii=False),
                    generated_by,
                    generated_at,
                    status,
                    created_at,
                    created_at,
                    fingerprint,
                ),
            )
            conn.commit()
        return self.get_conversation_chunk(chunk_id=chunk_id)  # type: ignore[return-value]

    def get_conversation_chunk(self, *, chunk_id: str) -> ConversationRecallRecord | None:
        with self._lock:
            row = self._conn_or_raise().execute(
                "SELECT * FROM conversation_recall WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
        return self._conversation_from_row(row) if row is not None else None

    def list_conversation_chunks(
        self,
        *,
        thread_id: str,
        limit: int = 20,
        include_inactive: bool = False,
    ) -> list[ConversationRecallRecord]:
        query = """
            SELECT * FROM conversation_recall
            WHERE thread_id = ?
        """
        if not include_inactive:
            query += " AND status = 'active'"
        query += "\nORDER BY updated_at DESC\nLIMIT ?"
        with self._lock:
            rows = self._conn_or_raise().execute(
                query,
                (thread_id, max(1, int(limit))),
            ).fetchall()
        return [self._conversation_from_row(row) for row in rows]

    def search_conversation_chunks(self, *, thread_id: str, query: str, limit: int = 3) -> list[ConversationRecallRecord]:
        normalized = str(query or "").strip().lower()
        if not normalized:
            return []
        tokens = [token for token in normalized.split() if len(token) >= 2][:8]
        if not tokens:
            tokens = [normalized]
        with self._lock:
            rows = self._conn_or_raise().execute(
                """
                SELECT * FROM conversation_recall
                WHERE thread_id = ? AND status = 'active'
                ORDER BY updated_at DESC
                LIMIT 60
                """,
                (thread_id,),
            ).fetchall()
        scored: list[tuple[int, ConversationRecallRecord]] = []
        for row in rows:
            record = self._conversation_from_row(row)
            haystack = f"{record.snippet} {record.summary} {' '.join(record.tags)}".lower()
            score = sum(1 for token in tokens if token in haystack)
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], item[1].updated_at))
        return [record for _, record in scored[: max(1, int(limit))]]

    def update_conversation_chunk_status(
        self,
        *,
        chunk_id: str,
        status: str,
        updated_at: str,
    ) -> ConversationRecallRecord | None:
        with self._lock:
            self._conn_or_raise().execute(
                "UPDATE conversation_recall SET status = ?, updated_at = ? WHERE chunk_id = ?",
                (status, updated_at, chunk_id),
            )
            self._conn_or_raise().commit()
        return self.get_conversation_chunk(chunk_id=chunk_id)

    def clear_conversation_chunks(self, *, thread_id: str) -> None:
        with self._lock:
            self._conn_or_raise().execute(
                "DELETE FROM conversation_recall WHERE thread_id = ?",
                (thread_id,),
            )
            self._conn_or_raise().commit()

    def delete_conversation_chunks_by_provenance(
        self,
        *,
        turn_id: str = "",
        run_id: str = "",
        thread_id: str | None = None,
    ) -> int:
        matches = self.list_conversation_chunks_by_provenance(
            turn_id=turn_id,
            run_id=run_id,
            thread_id=thread_id,
            limit=500,
        )
        if not matches:
            return 0
        with self._lock:
            conn = self._conn_or_raise()
            for record in matches:
                conn.execute("DELETE FROM conversation_recall WHERE chunk_id = ?", (record.chunk_id,))
            conn.commit()
        return len(matches)

    def record_consolidation_run(
        self,
        *,
        trigger: str,
        thread_id: str | None,
        status: str,
        created_at: str,
        completed_at: str,
        summary: dict[str, Any],
    ) -> ConsolidationRunSummary:
        consolidation_id = f"dream-{uuid4().hex}"
        with self._lock:
            self._conn_or_raise().execute(
                """
                INSERT INTO consolidation_runs (
                    consolidation_id, trigger, thread_id, status, created_at, completed_at, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    consolidation_id,
                    trigger,
                    thread_id,
                    status,
                    created_at,
                    completed_at,
                    json.dumps(dict(summary), ensure_ascii=False),
                ),
            )
            self._conn_or_raise().commit()
        return self.get_consolidation_run(consolidation_id=consolidation_id)  # type: ignore[return-value]

    def get_consolidation_run(self, *, consolidation_id: str) -> ConsolidationRunSummary | None:
        with self._lock:
            row = self._conn_or_raise().execute(
                "SELECT * FROM consolidation_runs WHERE consolidation_id = ?",
                (consolidation_id,),
            ).fetchone()
        return self._consolidation_from_row(row) if row is not None else None

    def latest_consolidation_run(self, *, thread_id: str | None = None) -> ConsolidationRunSummary | None:
        with self._lock:
            if thread_id:
                row = self._conn_or_raise().execute(
                    """
                    SELECT * FROM consolidation_runs
                    WHERE thread_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (thread_id,),
                ).fetchone()
            else:
                row = self._conn_or_raise().execute(
                    "SELECT * FROM consolidation_runs ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
        return self._consolidation_from_row(row) if row is not None else None

    def list_consolidation_runs(
        self,
        *,
        thread_id: str | None = None,
        limit: int = 10,
    ) -> list[ConsolidationRunSummary]:
        with self._lock:
            if thread_id:
                rows = self._conn_or_raise().execute(
                    """
                    SELECT * FROM consolidation_runs
                    WHERE thread_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (thread_id, max(1, int(limit))),
                ).fetchall()
            else:
                rows = self._conn_or_raise().execute(
                    "SELECT * FROM consolidation_runs ORDER BY created_at DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
        return [self._consolidation_from_row(row) for row in rows]

    def record_context_assembly(
        self,
        *,
        run_id: str,
        thread_id: str,
        call_site: str,
        created_at: str,
        assembly: ContextAssembly,
    ) -> None:
        with self._lock:
            self._conn_or_raise().execute(
                """
                INSERT INTO context_assemblies (
                    assembly_id, run_id, thread_id, call_site, path_kind, assembly_json, decision_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"ctx-{uuid4().hex}",
                    run_id,
                    thread_id,
                    call_site,
                    assembly.path_kind,
                    json.dumps(assembly.to_dict(), ensure_ascii=False),
                    json.dumps(assembly.decision.to_dict(), ensure_ascii=False),
                    created_at,
                ),
            )
            self._conn_or_raise().commit()

    def list_context_assemblies(
        self,
        *,
        thread_id: str | None = None,
        run_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM context_assemblies WHERE 1 = 1"
        params: list[Any] = []
        if thread_id:
            query += " AND thread_id = ?"
            params.append(thread_id)
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn_or_raise().execute(query, tuple(params)).fetchall()
        return [
            {
                "assembly_id": str(row["assembly_id"] or ""),
                "run_id": str(row["run_id"] or ""),
                "thread_id": str(row["thread_id"] or ""),
                "call_site": str(row["call_site"] or ""),
                "path_kind": str(row["path_kind"] or ""),
                "created_at": str(row["created_at"] or ""),
                "assembly": json.loads(str(row["assembly_json"] or "{}")),
                "decision": json.loads(str(row["decision_json"] or "{}")),
            }
            for row in rows
        ]

    def record_context_turn_snapshot(self, snapshot: ContextTurnSnapshot) -> None:
        with self._lock:
            self._conn_or_raise().execute(
                """
                INSERT INTO context_turns (
                    turn_id, session_id, run_id, thread_id, assistant_message_id, segment_index, call_site,
                    path_type, user_query, context_envelope_json, assembly_decision_json, budget_report_json,
                    selected_memory_ids_json, selected_artifact_ids_json, selected_evidence_ids_json,
                    selected_conversation_ids_json, dropped_items_json, truncation_reason, run_status,
                    resume_source, checkpoint_id, orchestration_engine, model_invoked, excluded_from_context,
                    excluded_at, exclusion_reason, call_ids_json, post_state_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(turn_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    assistant_message_id = excluded.assistant_message_id,
                    path_type = excluded.path_type,
                    user_query = excluded.user_query,
                    context_envelope_json = excluded.context_envelope_json,
                    assembly_decision_json = excluded.assembly_decision_json,
                    budget_report_json = excluded.budget_report_json,
                    selected_memory_ids_json = excluded.selected_memory_ids_json,
                    selected_artifact_ids_json = excluded.selected_artifact_ids_json,
                    selected_evidence_ids_json = excluded.selected_evidence_ids_json,
                    selected_conversation_ids_json = excluded.selected_conversation_ids_json,
                    dropped_items_json = excluded.dropped_items_json,
                    truncation_reason = excluded.truncation_reason,
                    run_status = excluded.run_status,
                    resume_source = excluded.resume_source,
                    checkpoint_id = excluded.checkpoint_id,
                    orchestration_engine = excluded.orchestration_engine,
                    model_invoked = excluded.model_invoked,
                    excluded_from_context = excluded.excluded_from_context,
                    excluded_at = excluded.excluded_at,
                    exclusion_reason = excluded.exclusion_reason,
                    call_ids_json = excluded.call_ids_json,
                    post_state_json = excluded.post_state_json,
                    created_at = excluded.created_at
                """,
                (
                    snapshot.turn_id,
                    snapshot.session_id,
                    snapshot.run_id,
                    snapshot.thread_id,
                    snapshot.assistant_message_id,
                    snapshot.segment_index,
                    snapshot.call_site,
                    snapshot.path_type,
                    snapshot.user_query,
                    json.dumps(snapshot.context_envelope.to_dict(), ensure_ascii=False),
                    json.dumps(snapshot.assembly_decision.to_dict(), ensure_ascii=False),
                    json.dumps(dict(snapshot.budget_report), ensure_ascii=False),
                    json.dumps(list(snapshot.selected_memory_ids), ensure_ascii=False),
                    json.dumps(list(snapshot.selected_artifact_ids), ensure_ascii=False),
                    json.dumps(list(snapshot.selected_evidence_ids), ensure_ascii=False),
                    json.dumps(list(snapshot.selected_conversation_ids), ensure_ascii=False),
                    json.dumps(list(snapshot.dropped_items), ensure_ascii=False),
                    snapshot.truncation_reason,
                    snapshot.run_status,
                    snapshot.resume_source,
                    snapshot.checkpoint_id,
                    snapshot.orchestration_engine,
                    1 if snapshot.model_invoked else 0,
                    1 if snapshot.excluded_from_context else 0,
                    snapshot.excluded_at,
                    snapshot.exclusion_reason,
                    json.dumps(list(snapshot.call_ids), ensure_ascii=False),
                    json.dumps(dict(snapshot.post_turn_state_snapshot), ensure_ascii=False),
                    snapshot.created_at,
                ),
            )
            self._conn_or_raise().commit()

    def list_context_turn_snapshots(
        self,
        *,
        session_id: str | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
        limit: int = 20,
    ) -> list[ContextTurnSnapshot]:
        query = "SELECT * FROM context_turns WHERE 1 = 1"
        params: list[Any] = []
        if session_id is not None:
            query += " AND session_id = ?"
            params.append(session_id)
        if thread_id is not None:
            query += " AND thread_id = ?"
            params.append(thread_id)
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn_or_raise().execute(query, tuple(params)).fetchall()
        return [self._context_turn_from_row(row) for row in rows]

    def get_context_turn_snapshot(
        self,
        *,
        turn_id: str,
        session_id: str | None = None,
    ) -> ContextTurnSnapshot | None:
        query = "SELECT * FROM context_turns WHERE turn_id = ?"
        params: list[Any] = [turn_id]
        if session_id is not None:
            query += " AND session_id = ?"
            params.append(session_id)
        with self._lock:
            row = self._conn_or_raise().execute(query, tuple(params)).fetchone()
        return self._context_turn_from_row(row) if row is not None else None

    def update_context_turn_exclusion(
        self,
        *,
        turn_id: str,
        excluded_from_context: bool,
        excluded_at: str,
        exclusion_reason: str,
    ) -> ContextTurnSnapshot | None:
        with self._lock:
            self._conn_or_raise().execute(
                """
                UPDATE context_turns
                SET excluded_from_context = ?, excluded_at = ?, exclusion_reason = ?
                WHERE turn_id = ?
                """,
                (1 if excluded_from_context else 0, excluded_at, exclusion_reason, turn_id),
            )
            self._conn_or_raise().commit()
        return self.get_context_turn_snapshot(turn_id=turn_id)

    def delete_context_turn_snapshot(self, *, turn_id: str) -> None:
        with self._lock:
            self._conn_or_raise().execute("DELETE FROM context_turns WHERE turn_id = ?", (turn_id,))
            self._conn_or_raise().execute("DELETE FROM context_model_calls WHERE turn_id = ?", (turn_id,))
            self._conn_or_raise().commit()

    def record_context_model_call(self, snapshot: ContextModelCallSnapshot) -> None:
        with self._lock:
            self._conn_or_raise().execute(
                """
                INSERT INTO context_model_calls (
                    call_id, session_id, run_id, thread_id, turn_id, call_type, call_site, path_type, user_query,
                    context_envelope_json, assembly_decision_json, budget_report_json, selected_memory_ids_json,
                    selected_artifact_ids_json, selected_evidence_ids_json, selected_conversation_ids_json,
                    dropped_items_json, truncation_reason, run_status, resume_source, checkpoint_id,
                    orchestration_engine, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    turn_id = excluded.turn_id,
                    call_type = excluded.call_type,
                    call_site = excluded.call_site,
                    path_type = excluded.path_type,
                    user_query = excluded.user_query,
                    context_envelope_json = excluded.context_envelope_json,
                    assembly_decision_json = excluded.assembly_decision_json,
                    budget_report_json = excluded.budget_report_json,
                    selected_memory_ids_json = excluded.selected_memory_ids_json,
                    selected_artifact_ids_json = excluded.selected_artifact_ids_json,
                    selected_evidence_ids_json = excluded.selected_evidence_ids_json,
                    selected_conversation_ids_json = excluded.selected_conversation_ids_json,
                    dropped_items_json = excluded.dropped_items_json,
                    truncation_reason = excluded.truncation_reason,
                    run_status = excluded.run_status,
                    resume_source = excluded.resume_source,
                    checkpoint_id = excluded.checkpoint_id,
                    orchestration_engine = excluded.orchestration_engine,
                    created_at = excluded.created_at
                """,
                (
                    snapshot.call_id,
                    snapshot.session_id,
                    snapshot.run_id,
                    snapshot.thread_id,
                    snapshot.turn_id,
                    snapshot.call_type,
                    snapshot.call_site,
                    snapshot.path_type,
                    snapshot.user_query,
                    json.dumps(snapshot.context_envelope.to_dict(), ensure_ascii=False),
                    json.dumps(snapshot.assembly_decision.to_dict(), ensure_ascii=False),
                    json.dumps(dict(snapshot.budget_report), ensure_ascii=False),
                    json.dumps(list(snapshot.selected_memory_ids), ensure_ascii=False),
                    json.dumps(list(snapshot.selected_artifact_ids), ensure_ascii=False),
                    json.dumps(list(snapshot.selected_evidence_ids), ensure_ascii=False),
                    json.dumps(list(snapshot.selected_conversation_ids), ensure_ascii=False),
                    json.dumps(list(snapshot.dropped_items), ensure_ascii=False),
                    snapshot.truncation_reason,
                    snapshot.run_status,
                    snapshot.resume_source,
                    snapshot.checkpoint_id,
                    snapshot.orchestration_engine,
                    snapshot.created_at,
                ),
            )
            self._conn_or_raise().commit()

    def list_context_model_calls(self, *, turn_id: str) -> list[ContextModelCallSnapshot]:
        with self._lock:
            rows = self._conn_or_raise().execute(
                """
                SELECT * FROM context_model_calls
                WHERE turn_id = ?
                ORDER BY created_at ASC
                """,
                (turn_id,),
            ).fetchall()
        return [self._context_model_call_from_row(row) for row in rows]

    def get_context_model_call(self, *, call_id: str, turn_id: str | None = None) -> ContextModelCallSnapshot | None:
        query = "SELECT * FROM context_model_calls WHERE call_id = ?"
        params: list[Any] = [call_id]
        if turn_id is not None:
            query += " AND turn_id = ?"
            params.append(turn_id)
        with self._lock:
            row = self._conn_or_raise().execute(query, tuple(params)).fetchone()
        return self._context_model_call_from_row(row) if row is not None else None

    def delete_context_model_calls(self, *, turn_id: str) -> None:
        with self._lock:
            self._conn_or_raise().execute("DELETE FROM context_model_calls WHERE turn_id = ?", (turn_id,))
            self._conn_or_raise().commit()

    def record_context_event(
        self,
        *,
        event_type: str,
        session_id: str | None,
        thread_id: str,
        created_at: str,
        payload: dict[str, Any] | None = None,
        run_id: str = "",
        turn_id: str = "",
    ) -> ContextAuditRecord:
        audit_id = f"ctxevt-{uuid4().hex}"
        with self._lock:
            self._conn_or_raise().execute(
                """
                INSERT INTO context_audit (
                    audit_id, event_type, session_id, thread_id, run_id, turn_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    event_type,
                    session_id,
                    thread_id,
                    run_id,
                    turn_id,
                    json.dumps(dict(payload or {}), ensure_ascii=False),
                    created_at,
                ),
            )
            self._conn_or_raise().commit()
        return ContextAuditRecord(
            audit_id=audit_id,
            event_type=event_type,
            session_id=session_id,
            thread_id=thread_id,
            run_id=run_id,
            turn_id=turn_id,
            created_at=created_at,
            payload=dict(payload or {}),
        )

    def list_context_events(self, *, thread_id: str, limit: int = 40) -> list[ContextAuditRecord]:
        with self._lock:
            rows = self._conn_or_raise().execute(
                """
                SELECT * FROM context_audit
                WHERE thread_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (thread_id, max(1, int(limit))),
            ).fetchall()
        return [self._audit_from_row(row) for row in rows]

    def list_memories_by_provenance(
        self,
        *,
        turn_id: str = "",
        run_id: str = "",
        include_inactive: bool = True,
        limit: int = 200,
    ) -> list[StoredMemory]:
        if not turn_id and not run_id:
            return []
        query = "SELECT * FROM memories WHERE 1 = 1"
        params: list[Any] = []
        if not include_inactive:
            query += " AND enabled = 1 AND status NOT IN ('dropped', 'invalidated')"
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn_or_raise().execute(query, tuple(params)).fetchall()
        matches: list[StoredMemory] = []
        for row in rows:
            record = self._memory_from_row(row)
            if turn_id and turn_id in record.source_turn_ids:
                matches.append(record)
                continue
            if run_id and run_id in record.source_run_ids:
                matches.append(record)
        return matches

    def list_conversation_chunks_by_provenance(
        self,
        *,
        turn_id: str = "",
        run_id: str = "",
        thread_id: str | None = None,
        limit: int = 200,
    ) -> list[ConversationRecallRecord]:
        query = "SELECT * FROM conversation_recall WHERE 1 = 1"
        params: list[Any] = []
        if thread_id is not None:
            query += " AND thread_id = ?"
            params.append(thread_id)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn_or_raise().execute(query, tuple(params)).fetchall()
        matches: list[ConversationRecallRecord] = []
        for row in rows:
            record = self._conversation_from_row(row)
            if turn_id and turn_id in record.source_turn_ids:
                matches.append(record)
                continue
            if run_id and run_id in record.source_run_ids:
                matches.append(record)
        return matches

    def _refresh_staleness_locked(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT memory_id, updated_at, stale_after, status
            FROM memories
            WHERE enabled = 1 AND status IN ('active', 'stale')
            """
        ).fetchall()
        changed = False
        for row in rows:
            next_freshness = freshness_state(str(row["updated_at"] or ""), str(row["stale_after"] or ""))
            next_status = "stale" if next_freshness == "stale" else "active"
            if next_status != str(row["status"] or ""):
                conn.execute(
                    "UPDATE memories SET freshness = ?, status = ? WHERE memory_id = ?",
                    (next_freshness, next_status, str(row["memory_id"] or "")),
                )
                changed = True
            else:
                conn.execute(
                    "UPDATE memories SET freshness = ? WHERE memory_id = ?",
                    (next_freshness, str(row["memory_id"] or "")),
                )
                changed = True
        if changed:
            conn.commit()

    def _default_memory_type(self, kind: MemoryKind | str, memory_type: str | None) -> str:
        if memory_type:
            return memory_type
        if kind == "procedural":
            return "workflow_rule"
        if kind == "episodic":
            return "session_episode"
        return "project_fact"

    def _default_scope(self, namespace: str, scope: str | None) -> str:
        if scope:
            return scope
        if namespace.startswith("user:"):
            return "user"
        if namespace.startswith("thread:"):
            return "thread"
        if namespace.startswith("global:"):
            return "global"
        return "project"

    def _memory_from_row(self, row: sqlite3.Row) -> StoredMemory:
        return StoredMemory(
            memory_id=str(row["memory_id"] or ""),
            kind=str(row["kind"] or "semantic"),  # type: ignore[arg-type]
            namespace=str(row["namespace"] or ""),
            memory_type=str(row["memory_type"] or self._default_memory_type(str(row["kind"] or "semantic"), None)),  # type: ignore[arg-type]
            scope=str(row["scope"] or self._default_scope(str(row["namespace"] or ""), None)),  # type: ignore[arg-type]
            title=str(row["title"] or ""),
            content=str(row["content"] or ""),
            summary=str(row["summary"] or ""),
            tags=tuple(json.loads(str(row["tags_json"] or "[]"))),
            metadata=dict(json.loads(str(row["metadata_json"] or "{}"))),
            source=str(row["source"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            confidence=float(row["confidence"] if row["confidence"] is not None else 0.5),
            freshness=str(row["freshness"] or "fresh"),  # type: ignore[arg-type]
            stale_after=str(row["stale_after"] or ""),
            status=str(row["status"] or "active"),  # type: ignore[arg-type]
            supersedes=tuple(json.loads(str(row["supersedes_json"] or "[]"))),
            applicability=dict(json.loads(str(row["applicability_json"] or "{}"))),
            direct_prompt=bool(row["direct_prompt"]),
            promotion_priority=int(row["promotion_priority"] or 0),
            conflict_flag=bool(row["conflict_flag"]),
            conflict_with=tuple(json.loads(str(row["conflict_with_json"] or "[]"))),
            source_turn_ids=tuple(json.loads(str(row["source_turn_ids_json"] or "[]"))),
            source_run_ids=tuple(json.loads(str(row["source_run_ids_json"] or "[]"))),
            source_memory_ids=tuple(json.loads(str(row["source_memory_ids_json"] or "[]"))),
            generated_by=str(row["generated_by"] or ""),
            generated_at=str(row["generated_at"] or ""),
            fingerprint=str(row["fingerprint"] or ""),
            enabled=bool(row["enabled"]),
        )

    def _conversation_from_row(self, row: sqlite3.Row) -> ConversationRecallRecord:
        return ConversationRecallRecord(
            chunk_id=str(row["chunk_id"] or ""),
            thread_id=str(row["thread_id"] or ""),
            session_id=str(row["session_id"]) if row["session_id"] is not None else None,
            run_id=str(row["run_id"] or ""),
            role=str(row["role"] or ""),
            source_message_id=str(row["source_message_id"] or ""),
            snippet=str(row["snippet"] or ""),
            summary=str(row["summary"] or ""),
            tags=tuple(json.loads(str(row["tags_json"] or "[]"))),
            metadata=dict(json.loads(str(row["metadata_json"] or "{}"))),
            source_turn_ids=tuple(json.loads(str(row["source_turn_ids_json"] or "[]"))),
            source_run_ids=tuple(json.loads(str(row["source_run_ids_json"] or "[]"))),
            source_memory_ids=tuple(json.loads(str(row["source_memory_ids_json"] or "[]"))),
            generated_by=str(row["generated_by"] or ""),
            generated_at=str(row["generated_at"] or ""),
            status=str(row["status"] or "active"),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            fingerprint=str(row["fingerprint"] or ""),
        )

    def _context_turn_from_row(self, row: sqlite3.Row) -> ContextTurnSnapshot:
        envelope_payload = dict(json.loads(str(row["context_envelope_json"] or "{}")))
        decision_payload = dict(json.loads(str(row["assembly_decision_json"] or "{}")))
        return ContextTurnSnapshot(
            turn_id=str(row["turn_id"] or ""),
            session_id=str(row["session_id"]) if row["session_id"] is not None else None,
            run_id=str(row["run_id"] or ""),
            thread_id=str(row["thread_id"] or ""),
            assistant_message_id=str(row["assistant_message_id"]) if row["assistant_message_id"] is not None else None,
            segment_index=int(row["segment_index"] or 0),
            call_site=str(row["call_site"] or ""),
            path_type=str(row["path_type"] or "direct_answer"),  # type: ignore[arg-type]
            user_query=str(row["user_query"] or ""),
            context_envelope=ContextEnvelope(
                system_block=str(envelope_payload.get("system_block", "") or ""),
                history_block=str(envelope_payload.get("history_block", "") or ""),
                working_memory_block=str(envelope_payload.get("working_memory_block", "") or ""),
                episodic_block=str(envelope_payload.get("episodic_block", "") or ""),
                semantic_block=str(envelope_payload.get("semantic_block", "") or ""),
                procedural_block=str(envelope_payload.get("procedural_block", "") or ""),
                conversation_block=str(envelope_payload.get("conversation_block", "") or ""),
                artifact_block=str(envelope_payload.get("artifact_block", "") or ""),
                evidence_block=str(envelope_payload.get("evidence_block", "") or ""),
                budget_report=dict(envelope_payload.get("budget_report", {}) or {}),
            ),
            assembly_decision=ContextAssemblyDecision(
                path_type=str(decision_payload.get("path_type", "direct_answer")),  # type: ignore[arg-type]
                selected_history_ids=tuple(decision_payload.get("selected_history_ids", []) or []),
                selected_memory_ids=tuple(decision_payload.get("selected_memory_ids", []) or []),
                selected_artifact_ids=tuple(decision_payload.get("selected_artifact_ids", []) or []),
                selected_evidence_ids=tuple(decision_payload.get("selected_evidence_ids", []) or []),
                selected_conversation_ids=tuple(decision_payload.get("selected_conversation_ids", []) or []),
                dropped_items=tuple(decision_payload.get("dropped_items", []) or []),
                truncation_reason=str(decision_payload.get("truncation_reason", "") or ""),
            ),
            budget_report=dict(json.loads(str(row["budget_report_json"] or "{}"))),
            selected_memory_ids=tuple(json.loads(str(row["selected_memory_ids_json"] or "[]"))),
            selected_artifact_ids=tuple(json.loads(str(row["selected_artifact_ids_json"] or "[]"))),
            selected_evidence_ids=tuple(json.loads(str(row["selected_evidence_ids_json"] or "[]"))),
            selected_conversation_ids=tuple(json.loads(str(row["selected_conversation_ids_json"] or "[]"))),
            dropped_items=tuple(json.loads(str(row["dropped_items_json"] or "[]"))),
            truncation_reason=str(row["truncation_reason"] or ""),
            run_status=str(row["run_status"] or "fresh"),
            resume_source=str(row["resume_source"] or ""),
            checkpoint_id=str(row["checkpoint_id"] or ""),
            orchestration_engine=str(row["orchestration_engine"] or "langgraph"),
            model_invoked=bool(row["model_invoked"]),
            excluded_from_context=bool(row["excluded_from_context"]),
            excluded_at=str(row["excluded_at"] or ""),
            exclusion_reason=str(row["exclusion_reason"] or ""),
            call_ids=tuple(json.loads(str(row["call_ids_json"] or "[]"))),
            post_turn_state_snapshot=dict(json.loads(str(row["post_state_json"] or "{}"))),
            created_at=str(row["created_at"] or ""),
        )

    def _context_model_call_from_row(self, row: sqlite3.Row) -> ContextModelCallSnapshot:
        envelope_payload = dict(json.loads(str(row["context_envelope_json"] or "{}")))
        decision_payload = dict(json.loads(str(row["assembly_decision_json"] or "{}")))
        return ContextModelCallSnapshot(
            call_id=str(row["call_id"] or ""),
            session_id=str(row["session_id"]) if row["session_id"] is not None else None,
            run_id=str(row["run_id"] or ""),
            thread_id=str(row["thread_id"] or ""),
            turn_id=str(row["turn_id"] or ""),
            call_type=str(row["call_type"] or ""),
            call_site=str(row["call_site"] or ""),
            path_type=str(row["path_type"] or "direct_answer"),  # type: ignore[arg-type]
            user_query=str(row["user_query"] or ""),
            context_envelope=ContextEnvelope(
                system_block=str(envelope_payload.get("system_block", "") or ""),
                history_block=str(envelope_payload.get("history_block", "") or ""),
                working_memory_block=str(envelope_payload.get("working_memory_block", "") or ""),
                episodic_block=str(envelope_payload.get("episodic_block", "") or ""),
                semantic_block=str(envelope_payload.get("semantic_block", "") or ""),
                procedural_block=str(envelope_payload.get("procedural_block", "") or ""),
                conversation_block=str(envelope_payload.get("conversation_block", "") or ""),
                artifact_block=str(envelope_payload.get("artifact_block", "") or ""),
                evidence_block=str(envelope_payload.get("evidence_block", "") or ""),
                budget_report=dict(envelope_payload.get("budget_report", {}) or {}),
            ),
            assembly_decision=ContextAssemblyDecision(
                path_type=str(decision_payload.get("path_type", "direct_answer")),  # type: ignore[arg-type]
                selected_history_ids=tuple(decision_payload.get("selected_history_ids", []) or []),
                selected_memory_ids=tuple(decision_payload.get("selected_memory_ids", []) or []),
                selected_artifact_ids=tuple(decision_payload.get("selected_artifact_ids", []) or []),
                selected_evidence_ids=tuple(decision_payload.get("selected_evidence_ids", []) or []),
                selected_conversation_ids=tuple(decision_payload.get("selected_conversation_ids", []) or []),
                dropped_items=tuple(decision_payload.get("dropped_items", []) or []),
                truncation_reason=str(decision_payload.get("truncation_reason", "") or ""),
            ),
            budget_report=dict(json.loads(str(row["budget_report_json"] or "{}"))),
            selected_memory_ids=tuple(json.loads(str(row["selected_memory_ids_json"] or "[]"))),
            selected_artifact_ids=tuple(json.loads(str(row["selected_artifact_ids_json"] or "[]"))),
            selected_evidence_ids=tuple(json.loads(str(row["selected_evidence_ids_json"] or "[]"))),
            selected_conversation_ids=tuple(json.loads(str(row["selected_conversation_ids_json"] or "[]"))),
            dropped_items=tuple(json.loads(str(row["dropped_items_json"] or "[]"))),
            truncation_reason=str(row["truncation_reason"] or ""),
            run_status=str(row["run_status"] or "fresh"),
            resume_source=str(row["resume_source"] or ""),
            checkpoint_id=str(row["checkpoint_id"] or ""),
            orchestration_engine=str(row["orchestration_engine"] or "langgraph"),
            created_at=str(row["created_at"] or ""),
        )

    def _audit_from_row(self, row: sqlite3.Row) -> ContextAuditRecord:
        return ContextAuditRecord(
            audit_id=str(row["audit_id"] or ""),
            event_type=str(row["event_type"] or ""),
            session_id=str(row["session_id"]) if row["session_id"] is not None else None,
            thread_id=str(row["thread_id"] or ""),
            run_id=str(row["run_id"] or ""),
            turn_id=str(row["turn_id"] or ""),
            created_at=str(row["created_at"] or ""),
            payload=dict(json.loads(str(row["payload_json"] or "{}"))),
        )

    def _consolidation_from_row(self, row: sqlite3.Row) -> ConsolidationRunSummary:
        summary = dict(json.loads(str(row["summary_json"] or "{}")))
        return ConsolidationRunSummary(
            consolidation_id=str(row["consolidation_id"] or ""),
            trigger=str(row["trigger"] or ""),
            thread_id=str(row["thread_id"]) if row["thread_id"] is not None else None,
            status=str(row["status"] or ""),
            created_at=str(row["created_at"] or ""),
            completed_at=str(row["completed_at"] or ""),
            promoted_memory_ids=tuple(summary.get("promoted_memory_ids", []) or []),
            superseded_memory_ids=tuple(summary.get("superseded_memory_ids", []) or []),
            stale_memory_ids=tuple(summary.get("stale_memory_ids", []) or []),
            dropped_memory_ids=tuple(summary.get("dropped_memory_ids", []) or []),
            conflict_memory_ids=tuple(summary.get("conflict_memory_ids", []) or []),
            notes=tuple(summary.get("notes", []) or []),
            stats=dict(summary.get("stats", {}) or {}),
        )


context_store = ContextStore()
