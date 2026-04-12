from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.execution_metadata import attach_execution_metadata
from src.backend.runtime.postgres_session_repository import PostgresSessionRepository
from src.backend.runtime.session_manager import FsSessionRepository


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare filesystem and Postgres session repository behavior.")
    parser.add_argument("--output", required=True, help="Output JSON path.")
    parser.add_argument("--postgres-dsn", default=str(os.getenv("RAGCLAW_POSTGRES_DSN") or os.getenv("RAGCLAW_TEST_POSTGRES_DSN") or ""))
    return parser.parse_args(argv)


def _normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(record.get("title", "") or ""),
        "compressed_context": str(record.get("compressed_context", "") or ""),
        "excluded_turn_ids": [str(item) for item in list(record.get("excluded_turn_ids", []) or [])],
        "excluded_run_ids": [str(item) for item in list(record.get("excluded_run_ids", []) or [])],
        "message_count": len(list(record.get("messages", []) or [])),
        "agent_history": record.get("agent_history", []),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_path = Path(args.output)
    postgres_dsn = str(args.postgres_dsn or "").strip()
    if not postgres_dsn:
        payload = attach_execution_metadata(
            {
                "started_at": datetime.now(UTC).isoformat(),
                "completed_at": datetime.now(UTC).isoformat(),
                "status": "blocked",
                "reason": "postgres dsn is required for session parity",
            },
            config={"postgres_dsn_configured": False},
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(output_path))
        return 2

    migrations_dir = PROJECT_ROOT / "backend" / "migrations"
    postgres_repository = PostgresSessionRepository(postgres_dsn, migrations_dir=migrations_dir)
    postgres_repository.reset_all()

    with tempfile.TemporaryDirectory(prefix="ragclaw-session-parity-") as temp_dir:
        fs_repository = FsSessionRepository(Path(temp_dir))
        session = fs_repository.create_session("Parity Session")
        session_id = session["id"]
        fs_repository.save_message(session_id, "user", "hello parity", message_id="msg-user")
        fs_repository.save_message(session_id, "assistant", "assistant parity", message_id="msg-assistant", turn_id="turn-1", run_id="run-1")
        fs_repository.exclude_turn_from_context(
            session_id=session_id,
            turn_id="turn-1",
            run_id="run-1",
            reason="parity",
            created_at="2026-04-11T22:30:00Z",
        )
        fs_repository.compress_history(session_id, "summary", 1)

        import_report = postgres_repository.import_from_filesystem(Path(temp_dir) / "sessions")
        fs_record = fs_repository.load_session_record(session_id)
        pg_record = postgres_repository.load_session_record(session_id)
        fs_record["agent_history"] = fs_repository.load_session_for_agent(session_id)
        pg_record["agent_history"] = postgres_repository.load_session_for_agent(session_id)

    expected = _normalized_record(fs_record)
    observed = _normalized_record(pg_record)
    mismatches = [
        key
        for key, value in expected.items()
        if observed.get(key) != value
    ]
    payload = attach_execution_metadata(
        {
            "started_at": datetime.now(UTC).isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "status": "passed" if not mismatches else "failed",
            "session_id": session_id,
            "import_report": import_report,
            "expected": expected,
            "observed": observed,
            "mismatches": mismatches,
        },
        config={"postgres_dsn_configured": True},
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output_path))
    print(json.dumps({"status": payload["status"], "mismatches": mismatches}, ensure_ascii=False, indent=2))
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
