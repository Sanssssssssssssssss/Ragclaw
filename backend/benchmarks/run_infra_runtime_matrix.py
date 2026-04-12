from __future__ import annotations

import argparse
import asyncio
import json
import math
import multiprocessing
import os
import statistics
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fakeredis

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.execution_metadata import attach_execution_metadata
from benchmarks.infra_capabilities import write_machine_capabilities
from src.backend.observability.dual_trace_store import DualWriteRunTraceRepository
from src.backend.runtime.backends import build_runtime_backends
from src.backend.runtime.postgres_session_repository import PostgresSessionRepository
from src.backend.runtime.redis_queue_backend import RedisLeaseSettings, RedisQueueBackend
from src.backend.runtime.runtime import build_harness_runtime
from src.backend.runtime.session_manager import FsSessionRepository


@dataclass
class _ScenarioStats:
    name: str
    total_runs: int
    completed_runs: int
    failed_runs: int
    throughput_runs_per_sec: float
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    latency_p99_ms: float | None
    queue_wait_p50_ms: float | None
    queue_wait_p95_ms: float | None
    max_active_runs: int
    same_session_serialization_violation_count: int
    parity_mismatch_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "total_runs": self.total_runs,
            "completed_runs": self.completed_runs,
            "failed_runs": self.failed_runs,
            "throughput_runs_per_sec": self.throughput_runs_per_sec,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "latency_p99_ms": self.latency_p99_ms,
            "queue_wait_p50_ms": self.queue_wait_p50_ms,
            "queue_wait_p95_ms": self.queue_wait_p95_ms,
            "max_active_runs": self.max_active_runs,
            "same_session_serialization_violation_count": self.same_session_serialization_violation_count,
            "parity_mismatch_count": self.parity_mismatch_count,
        }


def _session_crud_roundtrip(
    *,
    name: str,
    repository,
) -> dict[str, Any]:
    started = time.perf_counter()
    session = repository.create_session(f"{name} Session")
    session_id = str(session["id"])
    repository.save_message(session_id, "user", f"{name} hello", message_id=f"{name}-msg-1")
    repository.save_message(session_id, "assistant", f"{name} answer", message_id=f"{name}-msg-2", turn_id=f"{name}-turn-1", run_id=f"{name}-run-1")
    repository.rename_session(session_id, f"{name} Renamed")
    repository.archive_session(session_id)
    archived_record = repository.load_session_record(session_id)
    repository.delete_session(session_id)
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "name": name,
        "duration_ms": duration_ms,
        "archived_at": archived_record.get("archived_at"),
        "message_count_before_delete": len(list(archived_record.get("messages", []) or [])),
        "title_after_rename": archived_record.get("title"),
        "status": "passed",
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run infrastructure-focused runtime load and soak scenarios.")
    parser.add_argument("--output", required=True, help="Output JSON file.")
    parser.add_argument("--mode", choices=("local-only", "external-infra"), default="local-only")
    parser.add_argument("--require-external-infra", action="store_true")
    parser.add_argument("--load-runs", type=int, default=24)
    parser.add_argument("--load-concurrency", type=int, default=6)
    parser.add_argument("--same-session-runs", type=int, default=12)
    parser.add_argument("--same-session-concurrency", type=int, default=4)
    parser.add_argument("--soak-seconds", type=int, default=30)
    parser.add_argument("--soak-concurrency", type=int, default=4)
    parser.add_argument("--include-dualwrite", action="store_true")
    parser.add_argument("--include-redis", action="store_true")
    parser.add_argument("--postgres-dsn", default=str(os.getenv("RAGCLAW_POSTGRES_DSN") or os.getenv("POSTGRES_DSN") or ""))
    return parser.parse_args(argv)


def _quantile_ms(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


class _FakeExecutor:
    def __init__(self, *, delay_seconds: float, active_counts: dict[str, int], stats: dict[str, Any]) -> None:
        self._delay_seconds = delay_seconds
        self._active_counts = active_counts
        self._stats = stats
        self._lock = asyncio.Lock()

    async def execute(self, runtime, handle, *, message: str, history: list[dict[str, Any]]) -> None:
        session_id = str(getattr(handle.metadata, "session_id", "") or "")
        async with self._lock:
            self._active_counts[session_id] = self._active_counts.get(session_id, 0) + 1
            self._stats["total_active_runs"] += 1
            self._stats["max_active_runs"] = max(self._stats["max_active_runs"], self._stats["total_active_runs"])
            if self._active_counts[session_id] > 1:
                self._stats["serialization_violations"] += 1
        try:
            await runtime.emit(
                handle,
                "route.decided",
                {"intent": "direct_answer", "needs_tools": False, "needs_retrieval": False},
            )
            await asyncio.sleep(self._delay_seconds)
            await runtime.emit(
                handle,
                "answer.completed",
                {
                    "segment_index": 0,
                    "content": f"ok:{message}",
                    "final": True,
                    "model": "benchmark-stub",
                    "input_tokens": 8,
                    "output_tokens": 4,
                    "total_tokens": 12,
                    "cost_usd": 0.0,
                },
            )
        finally:
            async with self._lock:
                self._active_counts[session_id] = max(0, self._active_counts.get(session_id, 1) - 1)
                self._stats["total_active_runs"] = max(0, self._stats["total_active_runs"] - 1)


async def _run_scenario(
    *,
    name: str,
    base_dir: Path,
    session_ids: list[str],
    total_runs: int,
    concurrency: int,
    delay_seconds: float,
    trace_backend: str = "jsonl",
    postgres_dsn: str = "",
    queue_backend: str = "inmemory",
    redis_url: str = "",
) -> _ScenarioStats:
    previous_env = {
        "RAGCLAW_TRACE_BACKEND": os.getenv("RAGCLAW_TRACE_BACKEND"),
        "RAGCLAW_POSTGRES_DSN": os.getenv("RAGCLAW_POSTGRES_DSN"),
        "RAGCLAW_QUEUE_BACKEND": os.getenv("RAGCLAW_QUEUE_BACKEND"),
        "RAGCLAW_REDIS_URL": os.getenv("RAGCLAW_REDIS_URL"),
    }
    os.environ["RAGCLAW_TRACE_BACKEND"] = trace_backend
    os.environ["RAGCLAW_QUEUE_BACKEND"] = queue_backend
    if postgres_dsn:
        os.environ["RAGCLAW_POSTGRES_DSN"] = postgres_dsn
    elif "RAGCLAW_POSTGRES_DSN" in os.environ:
        del os.environ["RAGCLAW_POSTGRES_DSN"]
    if redis_url:
        os.environ["RAGCLAW_REDIS_URL"] = redis_url
    elif "RAGCLAW_REDIS_URL" in os.environ:
        del os.environ["RAGCLAW_REDIS_URL"]

    try:
        backends = build_runtime_backends(base_dir, now_factory=lambda: datetime.now(UTC).isoformat())
        runtime = build_harness_runtime(base_dir, backends=backends)
        active_counts: dict[str, int] = {}
        stats = {"serialization_violations": 0, "max_active_runs": 0, "total_active_runs": 0}
        executor = _FakeExecutor(delay_seconds=delay_seconds, active_counts=active_counts, stats=stats)
        latencies: list[float] = []
        queue_waits: list[float] = []
        completed = 0
        failed = 0
        run_ids: list[str] = []
        semaphore = asyncio.Semaphore(concurrency)
        started = time.perf_counter()

        async def _one_run(index: int) -> None:
            nonlocal completed, failed
            session_id = session_ids[index % len(session_ids)]
            async with semaphore:
                run_started = time.perf_counter()
                try:
                    async for event in runtime.run_with_executor(
                        user_message=f"{name}:{index}",
                        session_id=session_id,
                        executor=executor,
                        history=[],
                    ):
                        if event.name == "run.started":
                            run_ids.append(event.run_id)
                        if event.name == "run.dequeued":
                            queued_at = str(event.payload.get("queued_at", "") or "")
                            dequeued_at = str(event.payload.get("dequeued_at", "") or "")
                            if queued_at and dequeued_at:
                                queue_waits.append(
                                    max(
                                        0.0,
                                        (datetime.fromisoformat(dequeued_at.replace("Z", "+00:00")) - datetime.fromisoformat(queued_at.replace("Z", "+00:00"))).total_seconds() * 1000,
                                    )
                                )
                    latencies.append((time.perf_counter() - run_started) * 1000)
                    completed += 1
                except Exception:
                    failed += 1

        await asyncio.gather(*[_one_run(index) for index in range(total_runs)])
        duration = max(0.001, time.perf_counter() - started)

        parity_mismatch_count = 0
        if trace_backend == "dualwrite":
            repo = backends.trace_repository
            if isinstance(repo, DualWriteRunTraceRepository):
                for run_id in run_ids:
                    report = repo.postgres_repository.parity_report(run_id) or {}
                    if not report.get("ordering_match", False) or report.get("jsonl_checksum") != report.get("postgres_checksum"):
                        parity_mismatch_count += 1

        return _ScenarioStats(
            name=name,
            total_runs=total_runs,
            completed_runs=completed,
            failed_runs=failed,
            throughput_runs_per_sec=round(completed / duration, 3),
            latency_p50_ms=statistics.median(latencies) if latencies else None,
            latency_p95_ms=_quantile_ms(latencies, 0.95),
            latency_p99_ms=_quantile_ms(latencies, 0.99),
            queue_wait_p50_ms=statistics.median(queue_waits) if queue_waits else None,
            queue_wait_p95_ms=_quantile_ms(queue_waits, 0.95),
            max_active_runs=stats["max_active_runs"],
            same_session_serialization_violation_count=stats["serialization_violations"],
            parity_mismatch_count=parity_mismatch_count,
        )
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def _run_soak(
    *,
    base_dir: Path,
    concurrency: int,
    seconds: int,
) -> _ScenarioStats:
    runtime = build_harness_runtime(base_dir)
    active_counts: dict[str, int] = {}
    stats = {"serialization_violations": 0, "max_active_runs": 0, "total_active_runs": 0}
    executor = _FakeExecutor(delay_seconds=0.05, active_counts=active_counts, stats=stats)
    deadline = time.perf_counter() + seconds
    latencies: list[float] = []
    completed = 0
    failed = 0

    async def _worker(worker_id: int) -> None:
        nonlocal completed, failed
        while time.perf_counter() < deadline:
            started = time.perf_counter()
            try:
                async for _event in runtime.run_with_executor(
                    user_message=f"soak:{worker_id}",
                    session_id=f"soak-session-{worker_id % 4}",
                    executor=executor,
                    history=[],
                ):
                    pass
                latencies.append((time.perf_counter() - started) * 1000)
                completed += 1
            except Exception:
                failed += 1

    started_at = time.perf_counter()
    await asyncio.gather(*[_worker(index) for index in range(concurrency)])
    duration = max(0.001, time.perf_counter() - started_at)
    return _ScenarioStats(
        name="local_soak",
        total_runs=completed + failed,
        completed_runs=completed,
        failed_runs=failed,
        throughput_runs_per_sec=round(completed / duration, 3),
        latency_p50_ms=statistics.median(latencies) if latencies else None,
        latency_p95_ms=_quantile_ms(latencies, 0.95),
        latency_p99_ms=_quantile_ms(latencies, 0.99),
        queue_wait_p50_ms=None,
        queue_wait_p95_ms=None,
        max_active_runs=stats["max_active_runs"],
        same_session_serialization_violation_count=stats["serialization_violations"],
    )


def _redis_worker(redis_url: str, hold_seconds: float, result_queue) -> None:
    async def _run() -> None:
        backend = RedisQueueBackend.from_url(
            redis_url,
            settings=RedisLeaseSettings(
                namespace="ragclaw-benchmark",
                lease_ttl_seconds=1.5,
                heartbeat_interval_seconds=0.5,
                poll_interval_seconds=0.02,
            ),
            now_factory=lambda: "2026-04-11T21:00:00Z",
        )
        lease = await backend.acquire("bench-session", owner_id=f"worker-{multiprocessing.current_process().pid}")
        try:
            if lease.queued:
                await lease.wait_until_active(lambda: "2026-04-11T21:00:01Z")
            result_queue.put(("start", multiprocessing.current_process().pid, time.time()))
            await asyncio.sleep(hold_seconds)
            result_queue.put(("end", multiprocessing.current_process().pid, time.time()))
        finally:
            await backend.release(lease)
            await backend.close()

    asyncio.run(_run())


def _run_redis_process_scenario() -> dict[str, Any]:
    server = fakeredis.TcpFakeServer(("127.0.0.1", 0), server_type="redis")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        redis_url = f"redis://127.0.0.1:{server.server_address[1]}/0"
        ctx = multiprocessing.get_context("spawn")
        result_queue = ctx.Queue()
        first = ctx.Process(target=_redis_worker, args=(redis_url, 0.25, result_queue))
        second = ctx.Process(target=_redis_worker, args=(redis_url, 0.25, result_queue))
        started = time.perf_counter()
        first.start()
        time.sleep(0.05)
        second.start()
        first.join(timeout=15)
        second.join(timeout=15)
        events: list[tuple[str, int, float]] = []
        while not result_queue.empty():
            events.append(result_queue.get())
        windows: dict[int, dict[str, float]] = defaultdict(dict)
        for kind, pid, ts in events:
            windows[pid][kind] = ts
        ordered = sorted((payload["start"], payload["end"]) for payload in windows.values())
        overlap_violations = 0
        if len(ordered) >= 2:
            first_window, second_window = ordered[:2]
            if first_window[1] > second_window[0] + 0.02:
                overlap_violations += 1
        return {
            "name": "redis_same_session_two_process",
            "duration_seconds": round(time.perf_counter() - started, 3),
            "worker_exit_codes": [first.exitcode, second.exitcode],
            "event_count": len(events),
            "same_session_serialization_violation_count": overlap_violations,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def _async_main(args: argparse.Namespace) -> dict[str, Any]:
    base_dir = PROJECT_ROOT / "backend"
    output_path = Path(args.output)
    capabilities_path = output_path.with_name("machine_capabilities.json")
    capability_report = write_machine_capabilities(capabilities_path, postgres_dsn=args.postgres_dsn or None)
    scenarios: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    session_repository_roundtrips: list[dict[str, Any]] = []

    external_available = bool(capability_report.get("modes", {}).get("external_infra", {}).get("available", False))
    if args.require_external_infra and not external_available:
        raise RuntimeError(
            f"external infrastructure is required but unavailable; see {capabilities_path}"
        )

    if args.mode == "external-infra" and not external_available:
        blocked.append(
            {
                "name": "external_infra_mode",
                "reason": "external infrastructure mode requested but docker/direct Redis/Postgres controls are unavailable",
                "machine_capabilities_path": str(capabilities_path),
            }
        )

    scenarios.append(
        (
            await _run_scenario(
                name="local_many_sessions",
                base_dir=base_dir,
                session_ids=[f"session-{index}" for index in range(max(4, args.load_concurrency))],
                total_runs=args.load_runs,
                concurrency=args.load_concurrency,
                delay_seconds=0.05,
            )
        ).to_dict()
    )
    scenarios.append(
        (
            await _run_scenario(
                name="local_same_session",
                base_dir=base_dir,
                session_ids=["same-session"],
                total_runs=args.same_session_runs,
                concurrency=args.same_session_concurrency,
                delay_seconds=0.05,
            )
        ).to_dict()
    )

    if args.include_dualwrite and args.postgres_dsn:
        scenarios.append(
            (
                await _run_scenario(
                    name="dualwrite_many_sessions",
                    base_dir=base_dir,
                    session_ids=[f"dual-session-{index}" for index in range(max(4, args.load_concurrency))],
                    total_runs=args.load_runs,
                    concurrency=args.load_concurrency,
                    delay_seconds=0.05,
                    trace_backend="dualwrite",
                    postgres_dsn=args.postgres_dsn,
                )
            ).to_dict()
        )
    elif args.include_dualwrite:
        blocked.append(
            {
                "name": "dualwrite_many_sessions",
                "reason": "dual-write scenario skipped because --postgres-dsn was not configured",
            }
        )

    if args.include_redis:
        scenarios.append(_run_redis_process_scenario())

    scenarios.append(
        (
            await _run_soak(
                base_dir=base_dir,
                concurrency=args.soak_concurrency,
                seconds=args.soak_seconds,
            )
        ).to_dict()
    )

    session_repository_roundtrips.append(
        _session_crud_roundtrip(
            name="filesystem_session_crud",
            repository=FsSessionRepository(base_dir),
        )
    )
    if args.postgres_dsn:
        session_repository_roundtrips.append(
            _session_crud_roundtrip(
                name="postgres_session_crud",
                repository=PostgresSessionRepository(
                    args.postgres_dsn,
                    migrations_dir=PROJECT_ROOT / "backend" / "migrations",
                ),
            )
        )

    payload = {
        "started_at": datetime.now(UTC).isoformat(),
        "mode": args.mode,
        "scenarios": scenarios,
        "blocked": blocked,
        "session_repository_roundtrips": session_repository_roundtrips,
        "machine_capabilities_path": str(capabilities_path),
        "machine_capabilities": capability_report,
    }
    return attach_execution_metadata(
        payload,
        config={
            "mode": args.mode,
            "require_external_infra": args.require_external_infra,
            "load_runs": args.load_runs,
            "load_concurrency": args.load_concurrency,
            "same_session_runs": args.same_session_runs,
            "same_session_concurrency": args.same_session_concurrency,
            "soak_seconds": args.soak_seconds,
            "soak_concurrency": args.soak_concurrency,
            "include_dualwrite": args.include_dualwrite,
            "include_redis": args.include_redis,
            "postgres_dsn_configured": bool(args.postgres_dsn),
        },
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        payload = asyncio.run(_async_main(args))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output_path))
    print(json.dumps(payload["scenarios"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
