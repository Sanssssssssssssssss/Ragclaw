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
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE
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

            CREATE INDEX IF NOT EXISTS idx_memories_kind_namespace ON memories(kind, namespace, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_memories_conflict_key ON memories(conflict_key);
            CREATE INDEX IF NOT EXISTS idx_conversation_recall_thread ON conversation_recall(thread_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_consolidation_runs_created ON consolidation_runs(created_at DESC);
            """
        )
        self._ensure_memory_columns(conn)
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
                    SET updated_at = ?, confidence = ?, metadata_json = ?, summary = ?, content = ?, status = ?, enabled = 1
                    WHERE fingerprint = ?
                    """,
                    (
                        candidate.updated_at or candidate.created_at,
                        candidate.confidence,
                        json.dumps(dict(candidate.metadata), ensure_ascii=False),
                        candidate.summary,
                        candidate.content,
                        candidate.status,
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
                    conflict_with_json, enabled, fingerprint, conflict_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
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
                    conflict_flag = ?, conflict_with_json = ?, enabled = CASE WHEN ? = 'dropped' THEN 0 ELSE enabled END
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
                query += " AND enabled = 1 AND status != 'dropped'"
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
                query += " AND status != 'dropped'"
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
            sql = "SELECT * FROM memories WHERE enabled = 1 AND status != 'dropped'"
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
                    metadata_json, created_at, updated_at, fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def list_conversation_chunks(self, *, thread_id: str, limit: int = 20) -> list[ConversationRecallRecord]:
        with self._lock:
            rows = self._conn_or_raise().execute(
                """
                SELECT * FROM conversation_recall
                WHERE thread_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
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
                WHERE thread_id = ?
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
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            fingerprint=str(row["fingerprint"] or ""),
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
