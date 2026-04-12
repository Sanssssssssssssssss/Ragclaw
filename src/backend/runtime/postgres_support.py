from __future__ import annotations

from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row


def postgres_connect(dsn: str):
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=5)


def iter_migration_paths(migrations_dir: Path) -> Iterator[Path]:
    for path in sorted(Path(migrations_dir).glob("*.sql")):
        if path.is_file():
            yield path


def apply_postgres_migrations(dsn: str, migrations_dir: Path) -> None:
    with postgres_connect(dsn) as conn, conn.cursor() as cur:
        for path in iter_migration_paths(migrations_dir):
            cur.execute(path.read_text(encoding="utf-8"))


__all__ = ["apply_postgres_migrations", "iter_migration_paths", "postgres_connect"]
