from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from src.backend.runtime.postgres_support import apply_postgres_migrations, postgres_connect


def _timestamp_value(value: Any, *, default: float | None = None) -> float:
    if value is None:
        return float(default if default is not None else time.time())


def _pg_timestamptz(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    if hasattr(value, "timestamp"):
        return float(value.timestamp())
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default if default is not None else time.time())


class PostgresSessionRepository:
    """Postgres-backed session repository with behavior parity to the filesystem store."""

    def __init__(self, dsn: str, *, migrations_dir: Path) -> None:
        self._dsn = dsn
        self._migrations_dir = Path(migrations_dir)
        self.ensure_schema()

    def _connect(self):
        return postgres_connect(self._dsn)

    def ensure_schema(self) -> None:
        apply_postgres_migrations(self._dsn, self._migrations_dir)

    def _default_record(self, session_id: str, title: str = "New Session") -> dict[str, Any]:
        now = time.time()
        return {
            "id": session_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "compressed_context": "",
            "excluded_turn_ids": [],
            "excluded_run_ids": [],
            "turn_actions": [],
            "messages": [],
            "archived_at": None,
        }

    def _ensure_session_row(self, session_id: str, *, title: str = "New Session") -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sessions (
                    session_id,
                    title,
                    compressed_context,
                    excluded_turn_ids,
                    excluded_run_ids,
                    turn_actions
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (session_id, title, "", Jsonb([]), Jsonb([]), Jsonb([])),
            )

    def _session_row(self, session_id: str) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM sessions WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
        if row:
            return dict(row)
        self._ensure_session_row(session_id)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM sessions WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
        if not row:
            raise FileNotFoundError(f"session does not exist for session_id={session_id}")
        return dict(row)

    def _session_messages(self, session_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        where_archived = "" if include_archived else "AND archived_at IS NULL"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT message_id, turn_id, run_id, role, content, payload
                FROM session_messages
                WHERE session_id = %s {where_archived}
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
        messages: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row.get("payload") or {})
            message = {
                "role": str(row.get("role", "") or ""),
                "content": str(row.get("content", "") or ""),
            }
            for key in (
                "tool_calls",
                "retrieval_steps",
                "usage",
                "run_meta",
                "checkpoint_events",
                "hitl_events",
                "excluded_from_context",
            ):
                if key in payload:
                    message[key] = payload[key]
            if row.get("message_id"):
                message["message_id"] = str(row["message_id"])
            if row.get("turn_id"):
                message["turn_id"] = str(row["turn_id"])
            if row.get("run_id"):
                message["run_id"] = str(row["run_id"])
            messages.append(message)
        return messages

    def _write_session_metadata(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sessions (
                    session_id,
                    title,
                    compressed_context,
                    excluded_turn_ids,
                    excluded_run_ids,
                    turn_actions,
                    archived_at
                ) VALUES (
                    %(session_id)s,
                    %(title)s,
                    %(compressed_context)s,
                    %(excluded_turn_ids)s,
                    %(excluded_run_ids)s,
                    %(turn_actions)s,
                    %(archived_at)s
                )
                ON CONFLICT (session_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    compressed_context = EXCLUDED.compressed_context,
                    excluded_turn_ids = EXCLUDED.excluded_turn_ids,
                    excluded_run_ids = EXCLUDED.excluded_run_ids,
                    turn_actions = EXCLUDED.turn_actions,
                    archived_at = EXCLUDED.archived_at,
                    updated_at = NOW()
                """,
                {
                    "session_id": str(record["id"]),
                    "title": str(record.get("title", "New Session") or "New Session"),
                    "compressed_context": str(record.get("compressed_context", "") or ""),
                    "excluded_turn_ids": Jsonb(list(record.get("excluded_turn_ids", []) or [])),
                    "excluded_run_ids": Jsonb(list(record.get("excluded_run_ids", []) or [])),
                    "turn_actions": Jsonb(list(record.get("turn_actions", []) or [])),
                    "archived_at": _pg_timestamptz(record.get("archived_at")),
                },
            )
            cur.execute("SELECT * FROM sessions WHERE session_id = %s", (str(record["id"]),))
            row = cur.fetchone()
        return self._record_from_row(dict(row), messages=record.get("messages"))

    def _record_from_row(self, row: dict[str, Any], *, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        created_at = _timestamp_value(row.get("created_at"))
        updated_at = _timestamp_value(row.get("updated_at"), default=created_at)
        record = self._default_record(str(row.get("session_id", "") or ""), title=str(row.get("title", "") or "New Session"))
        record.update(
            {
                "created_at": created_at,
                "updated_at": updated_at,
                "compressed_context": str(row.get("compressed_context", "") or ""),
                "excluded_turn_ids": [str(item) for item in list(row.get("excluded_turn_ids") or [])],
                "excluded_run_ids": [str(item) for item in list(row.get("excluded_run_ids") or [])],
                "turn_actions": list(row.get("turn_actions") or []),
                "archived_at": row.get("archived_at").isoformat() if row.get("archived_at") else None,
            }
        )
        record["messages"] = list(messages) if messages is not None else self._session_messages(record["id"])
        return record

    def create_session(self, title: str = "New Session") -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        record = self._default_record(session_id, title=title)
        return self._write_session_metadata(record)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.session_id, s.title, s.created_at, s.updated_at, COUNT(m.id) AS message_count
                FROM sessions AS s
                LEFT JOIN session_messages AS m
                  ON m.session_id = s.session_id
                 AND m.archived_at IS NULL
                WHERE s.archived_at IS NULL
                GROUP BY s.session_id, s.title, s.created_at, s.updated_at
                ORDER BY s.updated_at DESC, s.created_at DESC
                """
            )
            rows = cur.fetchall()
        return [
            {
                "id": str(row.get("session_id", "") or ""),
                "title": str(row.get("title", "") or "New Session"),
                "created_at": _timestamp_value(row.get("created_at")),
                "updated_at": _timestamp_value(row.get("updated_at")),
                "message_count": int(row.get("message_count", 0) or 0),
            }
            for row in rows
        ]

    def load_session_record(self, session_id: str) -> dict[str, Any]:
        row = self._session_row(session_id)
        return self._record_from_row(row)

    def load_session(self, session_id: str) -> list[dict[str, Any]]:
        self._ensure_session_row(session_id)
        return self._session_messages(session_id)

    def load_session_for_agent(self, session_id: str) -> list[dict[str, str]]:
        record = self.load_session_record(session_id)
        merged: list[dict[str, str]] = []

        compressed_context = str(record.get("compressed_context", "") or "").strip()
        if compressed_context:
            merged.append({"role": "assistant", "content": f"[Conversation summary]\n{compressed_context}"})

        excluded_turn_ids = {str(item) for item in record.get("excluded_turn_ids", []) or []}
        excluded_run_ids = {str(item) for item in record.get("excluded_run_ids", []) or []}
        for message in record.get("messages", []):
            role = str(message.get("role", "") or "")
            content = str(message.get("content", "") or "")
            turn_id = str(message.get("turn_id", "") or "")
            run_id = str(message.get("run_id", "") or "")
            if bool(message.get("excluded_from_context")):
                continue
            if turn_id and turn_id in excluded_turn_ids:
                continue
            if run_id and run_id in excluded_run_ids:
                continue
            if role == "assistant" and merged and merged[-1]["role"] == "assistant":
                if content:
                    merged[-1]["content"] = f"{merged[-1]['content']}\n\n{content}" if merged[-1]["content"] else content
                continue
            merged.append({"role": role, "content": content})
        return [item for item in merged if item["role"] in {"user", "assistant"}]

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        retrieval_steps: list[dict[str, Any]] | None = None,
        usage: dict[str, int] | None = None,
        run_meta: dict[str, Any] | None = None,
        checkpoint_events: list[dict[str, Any]] | None = None,
        hitl_events: list[dict[str, Any]] | None = None,
        message_id: str | None = None,
        turn_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_session_row(session_id)
        effective_message_id = str(message_id or f"msg-{uuid.uuid4().hex}")
        payload: dict[str, Any] = {}
        if tool_calls:
            payload["tool_calls"] = list(tool_calls)
        if retrieval_steps:
            payload["retrieval_steps"] = list(retrieval_steps)
        if usage:
            payload["usage"] = dict(usage)
        if run_meta:
            payload["run_meta"] = dict(run_meta)
        if checkpoint_events:
            payload["checkpoint_events"] = list(checkpoint_events)
        if hitl_events:
            payload["hitl_events"] = list(hitl_events)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO session_messages (
                    session_id, message_id, turn_id, run_id, role, content, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, message_id) DO UPDATE SET
                    turn_id = EXCLUDED.turn_id,
                    run_id = EXCLUDED.run_id,
                    role = EXCLUDED.role,
                    content = EXCLUDED.content,
                    payload = EXCLUDED.payload,
                    archived_at = NULL
                """,
                (
                    session_id,
                    effective_message_id,
                    turn_id,
                    run_id,
                    role,
                    content,
                    Jsonb(payload),
                ),
            )
            cur.execute("UPDATE sessions SET updated_at = NOW() WHERE session_id = %s", (session_id,))

        message: dict[str, Any] = {"role": role, "content": content, "message_id": effective_message_id}
        if tool_calls:
            message["tool_calls"] = list(tool_calls)
        if retrieval_steps:
            message["retrieval_steps"] = list(retrieval_steps)
        if usage:
            message["usage"] = dict(usage)
        if run_meta:
            message["run_meta"] = dict(run_meta)
        if checkpoint_events:
            message["checkpoint_events"] = list(checkpoint_events)
        if hitl_events:
            message["hitl_events"] = list(hitl_events)
        if turn_id:
            message["turn_id"] = turn_id
        if run_id:
            message["run_id"] = run_id
        return message

    def exclude_turn_from_context(
        self,
        *,
        session_id: str,
        turn_id: str,
        run_id: str,
        reason: str,
        created_at: str,
    ) -> dict[str, Any]:
        record = self.load_session_record(session_id)
        excluded_turn_ids = [str(item) for item in record.get("excluded_turn_ids", []) or []]
        excluded_run_ids = [str(item) for item in record.get("excluded_run_ids", []) or []]
        changed = False
        if turn_id and turn_id not in excluded_turn_ids:
            excluded_turn_ids.append(turn_id)
            changed = True
        if run_id and run_id not in excluded_run_ids:
            excluded_run_ids.append(run_id)
            changed = True
        record["excluded_turn_ids"] = excluded_turn_ids
        record["excluded_run_ids"] = excluded_run_ids
        actions = list(record.get("turn_actions", []) or [])
        if not any(
            str(item.get("turn_id", "") or "") == turn_id and str(item.get("action", "") or "") == "exclude"
            for item in actions
            if isinstance(item, dict)
        ):
            actions.append(
                {
                    "turn_id": turn_id,
                    "run_id": run_id,
                    "action": "exclude",
                    "reason": reason,
                    "created_at": created_at,
                }
            )
            changed = True
        record["turn_actions"] = actions
        if changed:
            self._write_session_metadata(record)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE session_messages
                SET payload = jsonb_set(COALESCE(payload, '{}'::jsonb), '{excluded_from_context}', 'true'::jsonb),
                    archived_at = archived_at
                WHERE session_id = %s
                  AND (
                    (%s <> '' AND run_id = %s)
                    OR (%s <> '' AND turn_id = %s)
                  )
                """,
                (session_id, run_id, run_id, turn_id, turn_id),
            )
        return self.load_session_record(session_id)

    def hard_delete_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        run_id: str,
        reason: str,
        created_at: str,
    ) -> dict[str, Any]:
        record = self.load_session_record(session_id)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM session_messages
                WHERE session_id = %s
                  AND (
                    (%s <> '' AND run_id = %s)
                    OR (%s <> '' AND turn_id = %s)
                  )
                RETURNING id
                """,
                (session_id, run_id, run_id, turn_id, turn_id),
            )
            deleted_count = len(cur.fetchall())
        record["excluded_turn_ids"] = [item for item in list(record.get("excluded_turn_ids", []) or []) if str(item) != turn_id]
        record["excluded_run_ids"] = [item for item in list(record.get("excluded_run_ids", []) or []) if str(item) != run_id]
        actions = list(record.get("turn_actions", []) or [])
        actions.append(
            {
                "turn_id": turn_id,
                "run_id": run_id,
                "action": "hard_delete",
                "reason": reason,
                "created_at": created_at,
                "deleted_count": deleted_count,
            }
        )
        record["turn_actions"] = actions
        self._write_session_metadata(record)
        return self.load_session_record(session_id)

    def get_history(self, session_id: str) -> dict[str, Any]:
        return self.load_session_record(session_id)

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        record = self.load_session_record(session_id)
        record["title"] = str(title or "").strip() or "New Session"
        return self._write_session_metadata(record)

    def set_title(self, session_id: str, title: str) -> dict[str, Any]:
        return self.rename_session(session_id, title)

    def archive_session(self, session_id: str) -> dict[str, Any]:
        record = self.load_session_record(session_id)
        if record.get("archived_at"):
            return record
        record["archived_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return self._write_session_metadata(record)

    def delete_session(self, session_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))

    def compress_history(self, session_id: str, summary: str, n_messages: int) -> dict[str, int]:
        self._ensure_session_row(session_id)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM session_messages
                WHERE session_id = %s
                  AND archived_at IS NULL
                ORDER BY created_at ASC, id ASC
                LIMIT %s
                """,
                (session_id, n_messages),
            )
            ids = [int(row["id"]) for row in cur.fetchall()]
            if ids:
                cur.execute(
                    "UPDATE session_messages SET archived_at = NOW() WHERE id = ANY(%s)",
                    (ids,),
                )
            cur.execute(
                "SELECT COUNT(*) AS remaining_count FROM session_messages WHERE session_id = %s AND archived_at IS NULL",
                (session_id,),
            )
            remaining_count = int((cur.fetchone() or {}).get("remaining_count", 0) or 0)
        record = self.load_session_record(session_id)
        existing_summary = str(record.get("compressed_context", "") or "").strip()
        record["compressed_context"] = f"{existing_summary}\n---\n{summary.strip()}".strip() if existing_summary else str(summary or "").strip()
        self._write_session_metadata(record)
        return {"archived_count": len(ids), "remaining_count": remaining_count}

    def get_compressed_context(self, session_id: str) -> str:
        return str(self.load_session_record(session_id).get("compressed_context", "") or "")

    def import_from_filesystem(self, sessions_dir: Path) -> dict[str, Any]:
        sessions_path = Path(sessions_dir)
        imported_sessions = 0
        imported_messages = 0
        for path in sorted(sessions_path.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                session_id = path.stem
                record = self._default_record(session_id)
                record["messages"] = list(raw)
            else:
                session_id = str(raw.get("id", path.stem) or path.stem)
                record = self._default_record(session_id, title=str(raw.get("title", "New Session") or "New Session"))
                record.update(
                    {
                        "created_at": _timestamp_value(raw.get("created_at")),
                        "updated_at": _timestamp_value(raw.get("updated_at"), default=_timestamp_value(raw.get("created_at"))),
                        "compressed_context": str(raw.get("compressed_context", "") or ""),
                        "excluded_turn_ids": [str(item) for item in list(raw.get("excluded_turn_ids", []) or [])],
                        "excluded_run_ids": [str(item) for item in list(raw.get("excluded_run_ids", []) or [])],
                        "turn_actions": list(raw.get("turn_actions", []) or []),
                        "messages": list(raw.get("messages", []) or []),
                        "archived_at": raw.get("archived_at"),
                    }
                )
            self._write_session_metadata(record)
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM session_messages WHERE session_id = %s", (session_id,))
            for message in list(record.get("messages", []) or []):
                self.save_message(
                    session_id,
                    str(message.get("role", "") or ""),
                    str(message.get("content", "") or ""),
                    tool_calls=message.get("tool_calls"),
                    retrieval_steps=message.get("retrieval_steps"),
                    usage=message.get("usage"),
                    run_meta=message.get("run_meta"),
                    checkpoint_events=message.get("checkpoint_events"),
                    hitl_events=message.get("hitl_events"),
                    message_id=str(message.get("message_id", "") or "") or None,
                    turn_id=str(message.get("turn_id", "") or "") or None,
                    run_id=str(message.get("run_id", "") or "") or None,
                )
                if message.get("excluded_from_context"):
                    with self._connect() as conn, conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE session_messages
                            SET payload = jsonb_set(COALESCE(payload, '{}'::jsonb), '{excluded_from_context}', 'true'::jsonb)
                            WHERE session_id = %s AND message_id = %s
                            """,
                            (session_id, str(message.get("message_id", "") or "")),
                        )
                imported_messages += 1
            imported_sessions += 1
        return {"imported_sessions": imported_sessions, "imported_messages": imported_messages}

    def reset_all(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE session_messages, sessions RESTART IDENTITY CASCADE")


__all__ = ["PostgresSessionRepository"]
