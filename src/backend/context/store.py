from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from src.backend.context.models import ContextAssembly, EpisodicSummary, MemoryKind, StoredMemory, WorkingMemory


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
            self._ensure_schema(conn)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = None
            self._db_path = None

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
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                fingerprint TEXT NOT NULL UNIQUE
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
            """
        )
        conn.commit()

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
    ) -> StoredMemory:
        memory_id = f"mem-{uuid4().hex}"
        with self._lock:
            existing = self._conn_or_raise().execute(
                "SELECT * FROM memories WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if existing is not None:
                return self._memory_from_row(existing)
            self._conn_or_raise().execute(
                """
                INSERT INTO memories (
                    memory_id, kind, namespace, title, content, summary, tags_json, metadata_json, source, created_at, updated_at, enabled, fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    memory_id,
                    kind,
                    namespace,
                    title,
                    content,
                    summary,
                    json.dumps(list(tags), ensure_ascii=False),
                    json.dumps(dict(metadata or {}), ensure_ascii=False),
                    source,
                    created_at,
                    created_at,
                    fingerprint,
                ),
            )
            self._conn_or_raise().commit()
        return self.get_memory(memory_id=memory_id)  # type: ignore[return-value]

    def get_memory(self, *, memory_id: str) -> StoredMemory | None:
        with self._lock:
            row = self._conn_or_raise().execute(
                "SELECT * FROM memories WHERE memory_id = ?",
                (memory_id,),
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
    ) -> StoredMemory | None:
        current = self.get_memory(memory_id=memory_id)
        if current is None:
            return None
        with self._lock:
            self._conn_or_raise().execute(
                """
                UPDATE memories
                SET title = ?, content = ?, summary = ?, tags_json = ?, metadata_json = ?, updated_at = ?
                WHERE memory_id = ?
                """,
                (
                    str(title or current.title),
                    str(content or current.content),
                    str(summary if summary is not None else current.summary),
                    json.dumps(list(tags if tags is not None else current.tags), ensure_ascii=False),
                    json.dumps(dict(metadata if metadata is not None else current.metadata), ensure_ascii=False),
                    updated_at,
                    memory_id,
                ),
            )
            self._conn_or_raise().commit()
        return self.get_memory(memory_id=memory_id)

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
    ) -> list[StoredMemory]:
        query = "SELECT * FROM memories WHERE enabled = 1"
        params: list[Any] = []
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        if namespace is not None:
            query += " AND namespace = ?"
            params.append(namespace)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn_or_raise().execute(query, tuple(params)).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def search_memories(
        self,
        *,
        kind: MemoryKind,
        namespaces: list[str] | tuple[str, ...],
        query: str,
        limit: int = 5,
    ) -> list[StoredMemory]:
        normalized = str(query or "").strip()
        if not normalized:
            return []
        namespace_values = [str(item).strip() for item in namespaces if str(item).strip()]
        pattern = f"%{normalized.lower()}%"
        if namespace_values:
            placeholders = ", ".join("?" for _ in namespace_values)
            sql = (
                "SELECT * FROM memories WHERE enabled = 1 AND kind = ? "
                f"AND namespace IN ({placeholders}) "
                "AND (lower(content) LIKE ? OR lower(summary) LIKE ? OR lower(title) LIKE ?) "
                "ORDER BY updated_at DESC LIMIT ?"
            )
            params: list[Any] = [kind, *namespace_values, pattern, pattern, pattern, max(1, int(limit))]
        else:
            sql = (
                "SELECT * FROM memories WHERE enabled = 1 AND kind = ? "
                "AND (lower(content) LIKE ? OR lower(summary) LIKE ? OR lower(title) LIKE ?) "
                "ORDER BY updated_at DESC LIMIT ?"
            )
            params = [kind, pattern, pattern, pattern, max(1, int(limit))]
        with self._lock:
            rows = self._conn_or_raise().execute(sql, tuple(params)).fetchall()
        return [self._memory_from_row(row) for row in rows]

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

    def _memory_from_row(self, row: sqlite3.Row) -> StoredMemory:
        return StoredMemory(
            memory_id=str(row["memory_id"] or ""),
            kind=str(row["kind"] or "semantic"),  # type: ignore[arg-type]
            namespace=str(row["namespace"] or ""),
            title=str(row["title"] or ""),
            content=str(row["content"] or ""),
            summary=str(row["summary"] or ""),
            tags=tuple(json.loads(str(row["tags_json"] or "[]"))),
            metadata=dict(json.loads(str(row["metadata_json"] or "{}"))),
            source=str(row["source"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            enabled=bool(row["enabled"]),
        )


context_store = ContextStore()
