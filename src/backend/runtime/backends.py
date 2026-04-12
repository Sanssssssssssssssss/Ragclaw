from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from src.backend.observability.trace_store import JsonlRunTraceRepository
from src.backend.observability.dual_trace_store import DualWriteRunTraceRepository
from src.backend.observability.postgres_trace_store import PostgresRunTraceRepository
from src.backend.runtime.hitl_repository import SqliteHitlRepository
from src.backend.runtime.policy import InMemoryQueueBackend, QueueLease
from src.backend.runtime.postgres_session_repository import PostgresSessionRepository
from src.backend.runtime.session_manager import FsSessionRepository


class SessionRepository(Protocol):
    def create_session(self, title: str = "New Session") -> dict[str, Any]: ...
    def list_sessions(self) -> list[dict[str, Any]]: ...
    def load_session_record(self, session_id: str) -> dict[str, Any]: ...
    def load_session(self, session_id: str) -> list[dict[str, Any]]: ...
    def load_session_for_agent(self, session_id: str) -> list[dict[str, str]]: ...
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
    ) -> dict[str, Any]: ...
    def exclude_turn_from_context(
        self,
        *,
        session_id: str,
        turn_id: str,
        run_id: str,
        reason: str,
        created_at: str,
    ) -> dict[str, Any]: ...
    def hard_delete_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        run_id: str,
        reason: str,
        created_at: str,
    ) -> dict[str, Any]: ...
    def get_history(self, session_id: str) -> dict[str, Any]: ...
    def rename_session(self, session_id: str, title: str) -> dict[str, Any]: ...
    def set_title(self, session_id: str, title: str) -> dict[str, Any]: ...
    def archive_session(self, session_id: str) -> dict[str, Any]: ...
    def delete_session(self, session_id: str) -> None: ...
    def compress_history(self, session_id: str, summary: str, n_messages: int) -> dict[str, int]: ...
    def get_compressed_context(self, session_id: str) -> str: ...


class RunTraceRepository(Protocol):
    def paths_for(self, run_id: str): ...
    def create_run(self, metadata): ...
    def append_event(self, event) -> None: ...
    def finalize_run(self, run_id: str, outcome): ...
    def read_trace(self, run_id: str) -> dict[str, Any]: ...


class QueueBackend(Protocol):
    async def acquire(self, session_id: str | None, *, owner_id: str | None = None) -> QueueLease: ...
    async def is_active(self, lease: QueueLease) -> bool: ...
    async def heartbeat(self, lease: QueueLease) -> bool: ...
    async def release(self, lease_or_session: QueueLease | str | None) -> None: ...


class HitlRepository(Protocol):
    @property
    def saver(self) -> Any: ...

    def configure_for_base_dir(self, base_dir: Path) -> None: ...
    def close(self) -> None: ...
    def thread_id_for(self, session_id: str | None, run_id: str | None) -> str: ...
    def list_thread_checkpoints(self, thread_id: str): ...
    def get_checkpoint(self, thread_id: str, checkpoint_id: str): ...
    def latest_checkpoint(self, thread_id: str): ...
    def pending_hitl(self, thread_id: str): ...
    def list_pending_hitl(self, limit: int = 50): ...
    def list_hitl_requests(self, thread_id: str): ...
    def get_hitl_request(self, *, thread_id: str | None = None, checkpoint_id: str | None = None, request_id: str | None = None): ...
    def get_hitl_decision(self, *, thread_id: str | None = None, checkpoint_id: str | None = None, request_id: str | None = None): ...
    def record_pending_hitl(self, request): ...
    def record_hitl_decision(self, *, thread_id: str, checkpoint_id: str, decision: str, actor_id: str, actor_type: str, decided_at: str, resume_source: str, approved_input_snapshot: dict[str, Any] | None = None, edited_input_snapshot: dict[str, Any] | None = None, rejected_input_snapshot: dict[str, Any] | None = None): ...


@dataclass(frozen=True)
class RuntimeBackendConfig:
    session_backend: str = "filesystem"
    trace_backend: str = "jsonl"
    queue_backend: str = "inmemory"
    hitl_backend: str = "sqlite"
    postgres_dsn: str | None = None
    redis_url: str | None = None
    queue_namespace: str = "ragclaw"
    queue_lease_ttl_seconds: float = 30.0
    queue_heartbeat_interval_seconds: float = 10.0
    queue_poll_interval_seconds: float = 0.25


@dataclass(frozen=True)
class RuntimeBackends:
    config: RuntimeBackendConfig
    session_repository: SessionRepository
    trace_repository: RunTraceRepository
    queue_backend: QueueBackend
    hitl_repository: HitlRepository


def _normalized_backend(value: str | None, *, default: str, aliases: dict[str, str]) -> str:
    raw = str(value or default).strip().lower()
    return aliases.get(raw, raw)


def load_runtime_backend_config() -> RuntimeBackendConfig:
    redis_url = os.getenv("RAGCLAW_REDIS_URL")
    postgres_dsn = os.getenv("RAGCLAW_POSTGRES_DSN") or os.getenv("POSTGRES_DSN")
    queue_namespace = str(os.getenv("RAGCLAW_QUEUE_NAMESPACE") or "ragclaw").strip() or "ragclaw"

    def _float_env(name: str, *, default: float) -> float:
        raw = os.getenv(name)
        if raw is None or not str(raw).strip():
            return default
        try:
            return float(str(raw).strip())
        except ValueError as exc:
            raise ValueError(f"Invalid float value for {name}: {raw}") from exc

    return RuntimeBackendConfig(
        session_backend=_normalized_backend(
            os.getenv("RAGCLAW_SESSION_BACKEND"),
            default="filesystem",
            aliases={"fs": "filesystem", "local": "filesystem", "pg": "postgres"},
        ),
        trace_backend=_normalized_backend(
            os.getenv("RAGCLAW_TRACE_BACKEND"),
            default="jsonl",
            aliases={
                "local": "jsonl",
                "pg": "postgres",
                "dualwrite-postgres": "dualwrite",
                "dualwrite_pg": "dualwrite",
            },
        ),
        queue_backend=_normalized_backend(
            os.getenv("RAGCLAW_QUEUE_BACKEND"),
            default="inmemory",
            aliases={"memory": "inmemory", "local": "inmemory"},
        ),
        hitl_backend=_normalized_backend(
            os.getenv("RAGCLAW_HITL_BACKEND"),
            default="sqlite",
            aliases={"local": "sqlite"},
        ),
        postgres_dsn=str(postgres_dsn).strip() if postgres_dsn and str(postgres_dsn).strip() else None,
        redis_url=str(redis_url).strip() if redis_url and str(redis_url).strip() else None,
        queue_namespace=queue_namespace,
        queue_lease_ttl_seconds=_float_env("RAGCLAW_QUEUE_LEASE_TTL_SECONDS", default=30.0),
        queue_heartbeat_interval_seconds=_float_env("RAGCLAW_QUEUE_HEARTBEAT_INTERVAL_SECONDS", default=10.0),
        queue_poll_interval_seconds=_float_env("RAGCLAW_QUEUE_POLL_INTERVAL_SECONDS", default=0.25),
    )


def build_runtime_backends(base_dir: Path, *, now_factory: Callable[[], str]) -> RuntimeBackends:
    config = load_runtime_backend_config()

    if config.session_backend not in {"filesystem", "postgres"}:
        raise ValueError(f"Unsupported session backend: {config.session_backend}")
    if config.trace_backend not in {"jsonl", "postgres", "dualwrite"}:
        raise ValueError(f"Unsupported trace backend: {config.trace_backend}")
    if config.queue_backend not in {"inmemory", "redis"}:
        raise ValueError(f"Unsupported queue backend: {config.queue_backend}")
    if config.hitl_backend != "sqlite":
        raise ValueError(f"Unsupported HITL backend: {config.hitl_backend}")

    hitl_repository = SqliteHitlRepository(base_dir=base_dir)
    jsonl_trace_repository = JsonlRunTraceRepository(Path(base_dir) / "storage" / "runs")
    if config.trace_backend == "postgres":
        if not config.postgres_dsn:
            raise ValueError("RAGCLAW_POSTGRES_DSN is required when RAGCLAW_TRACE_BACKEND=postgres")
        trace_repository: RunTraceRepository = PostgresRunTraceRepository(
            config.postgres_dsn,
            migrations_dir=Path(base_dir).parent / "backend" / "migrations",
        )
    elif config.trace_backend == "dualwrite":
        if not config.postgres_dsn:
            raise ValueError("RAGCLAW_POSTGRES_DSN is required when RAGCLAW_TRACE_BACKEND=dualwrite")
        trace_repository = DualWriteRunTraceRepository(
            jsonl_repository=jsonl_trace_repository,
            postgres_repository=PostgresRunTraceRepository(
                config.postgres_dsn,
                migrations_dir=Path(base_dir).parent / "backend" / "migrations",
            ),
        )
    else:
        trace_repository = jsonl_trace_repository

    if config.queue_backend == "redis":
        from src.backend.runtime.redis_queue_backend import RedisLeaseSettings, RedisQueueBackend

        if not config.redis_url:
            raise ValueError("RAGCLAW_REDIS_URL is required when RAGCLAW_QUEUE_BACKEND=redis")
        queue_backend: QueueBackend = RedisQueueBackend.from_url(
            config.redis_url,
            settings=RedisLeaseSettings(
                namespace=config.queue_namespace,
                lease_ttl_seconds=config.queue_lease_ttl_seconds,
                heartbeat_interval_seconds=config.queue_heartbeat_interval_seconds,
                poll_interval_seconds=config.queue_poll_interval_seconds,
            ),
            now_factory=now_factory,
        )
    else:
        queue_backend = InMemoryQueueBackend(now_factory)

    if config.session_backend == "postgres":
        if not config.postgres_dsn:
            raise ValueError("RAGCLAW_POSTGRES_DSN is required when RAGCLAW_SESSION_BACKEND=postgres")
        session_repository: SessionRepository = PostgresSessionRepository(
            config.postgres_dsn,
            migrations_dir=Path(base_dir).parent / "backend" / "migrations",
        )
    else:
        session_repository = FsSessionRepository(base_dir)

    return RuntimeBackends(
        config=config,
        session_repository=session_repository,
        trace_repository=trace_repository,
        queue_backend=queue_backend,
        hitl_repository=hitl_repository,
    )


__all__ = [
    "HitlRepository",
    "QueueBackend",
    "RunTraceRepository",
    "RuntimeBackendConfig",
    "RuntimeBackends",
    "SessionRepository",
    "build_runtime_backends",
    "load_runtime_backend_config",
]
