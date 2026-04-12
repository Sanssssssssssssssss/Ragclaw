from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass
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
from benchmarks.infra_capabilities import write_machine_capabilities
from src.backend.observability.dual_trace_store import DualWriteRunTraceRepository
from src.backend.observability.postgres_trace_store import PostgresRunTraceRepository
from src.backend.observability.trace_store import JsonlRunTraceRepository
from src.backend.observability.types import HarnessEvent, RunMetadata, RunOutcome
from src.backend.runtime.postgres_session_repository import PostgresSessionRepository
from src.backend.runtime.redis_queue_backend import RedisLeaseSettings, RedisQueueBackend


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_command(command: list[str], *, timeout: float = 30.0, capture_output: bool = True) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=capture_output,
        stdout=None if capture_output else subprocess.DEVNULL,
        stderr=None if capture_output else subprocess.DEVNULL,
        text=True,
        timeout=timeout,
        check=False,
    )
    return int(completed.returncode), str(getattr(completed, "stdout", "") or ""), str(getattr(completed, "stderr", "") or "")


def _wait_for_postgres(dsn: str, *, timeout_seconds: float = 30.0) -> None:
    import psycopg

    deadline = time.time() + timeout_seconds
    last_error = ""
    while time.time() < deadline:
        try:
            with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return
        except Exception as exc:  # pragma: no cover - environment dependent
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"postgres did not become ready within {timeout_seconds}s: {last_error}")


@dataclass
class _DockerService:
    image: str
    name: str
    ports: dict[int, int]
    env: dict[str, str]
    container_id: str = ""

    def start(self) -> None:
        command = ["docker", "run", "-d", "--name", self.name]
        for host_port, container_port in self.ports.items():
            command.extend(["-p", f"127.0.0.1:{host_port}:{container_port}"])
        for key, value in self.env.items():
            command.extend(["-e", f"{key}={value}"])
        command.append(self.image)
        code, stdout, stderr = _run_command(command, timeout=60.0)
        if code != 0:
            raise RuntimeError(f"docker run failed ({code}): {stderr or stdout}")
        self.container_id = stdout.strip()

    def stop(self) -> None:
        target = self.container_id or self.name
        if not target:
            return
        _run_command(["docker", "stop", target], timeout=30.0)

    def restart(self) -> None:
        target = self.container_id or self.name
        if not target:
            self.start()
            return
        code, stdout, stderr = _run_command(["docker", "start", target], timeout=30.0)
        if code != 0:
            raise RuntimeError(f"docker start failed ({code}): {stderr or stdout}")

    def cleanup(self) -> None:
        target = self.container_id or self.name
        if not target:
            return
        _run_command(["docker", "rm", "-f", target], timeout=30.0)
        self.container_id = ""


@dataclass
class _DirectRedisService:
    executable: str
    port: int
    process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        self.process = subprocess.Popen(
            [
                self.executable,
                "--save",
                "",
                "--appendonly",
                "no",
                "--port",
                str(self.port),
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.75)
        if self.process.poll() is not None:
            stdout, stderr = self.process.communicate(timeout=5)
            raise RuntimeError(f"redis-server exited early: {stderr or stdout}")

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process = None


def _default_postgres_start_command() -> list[str]:
    return [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(PROJECT_ROOT / "backend" / "scripts" / "dev" / "start-postgres-local.ps1"),
    ]


def _default_postgres_stop_command() -> list[str]:
    return [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(PROJECT_ROOT / "backend" / "scripts" / "dev" / "stop-postgres-local.ps1"),
    ]


async def _redis_lease_expiry_drill(redis_url: str) -> dict[str, Any]:
    backend_one = RedisQueueBackend.from_url(
        redis_url,
        settings=RedisLeaseSettings(namespace="ragclaw-external-drill", lease_ttl_seconds=0.4, heartbeat_interval_seconds=0.1, poll_interval_seconds=0.02),
        now_factory=lambda: datetime.now(UTC).isoformat(),
    )
    backend_two = RedisQueueBackend.from_url(
        redis_url,
        settings=RedisLeaseSettings(namespace="ragclaw-external-drill", lease_ttl_seconds=0.4, heartbeat_interval_seconds=0.1, poll_interval_seconds=0.02),
        now_factory=lambda: datetime.now(UTC).isoformat(),
    )
    try:
        first = await backend_one.acquire("external-session", owner_id="run-1")
        second = await backend_two.acquire("external-session", owner_id="run-2")
        await asyncio.sleep(0.5)
        await second.wait_until_active(lambda: datetime.now(UTC).isoformat())
        first_active = await backend_one.is_active(first)
        second_active = await backend_two.is_active(second)
        violation_count = 1 if first_active and second_active else 0
        return {
            "name": "redis_lease_expiry_drill",
            "status": "passed" if violation_count == 0 and second_active else "failed",
            "queued_second_lease": bool(second.queued),
            "first_active_after_expiry": bool(first_active),
            "second_active_after_expiry": bool(second_active),
            "same_session_serialization_violation_count": violation_count,
        }
    finally:
        await backend_one.close()
        await backend_two.close()


async def _redis_restart_drill(redis_url: str, stop_service, start_service) -> dict[str, Any]:
    backend_one = RedisQueueBackend.from_url(
        redis_url,
        settings=RedisLeaseSettings(namespace="ragclaw-external-restart", lease_ttl_seconds=1.0, heartbeat_interval_seconds=0.2, poll_interval_seconds=0.02),
        now_factory=lambda: datetime.now(UTC).isoformat(),
    )
    try:
        lease = await backend_one.acquire("external-session", owner_id="run-restart-1")
        stop_service()
        await asyncio.sleep(0.5)
        start_service()
        await asyncio.sleep(0.5)
        backend_two = RedisQueueBackend.from_url(
            redis_url,
            settings=RedisLeaseSettings(namespace="ragclaw-external-restart", lease_ttl_seconds=1.0, heartbeat_interval_seconds=0.2, poll_interval_seconds=0.02),
            now_factory=lambda: datetime.now(UTC).isoformat(),
        )
        try:
            second = await backend_two.acquire("external-session", owner_id="run-restart-2")
            first_active = False
            try:
                first_active = await backend_one.is_active(lease)
            except Exception:
                first_active = False
            second_active = await backend_two.is_active(second)
            violation_count = 1 if first_active and second_active else 0
            return {
                "name": "redis_restart_drill",
                "status": "passed" if violation_count == 0 and second_active else "failed",
                "first_active_after_restart": bool(first_active),
                "second_active_after_restart": bool(second_active),
                "same_session_serialization_violation_count": violation_count,
            }
        finally:
            await backend_two.close()
    finally:
        await backend_one.close()


def _postgres_retry_drill(
    *,
    postgres_dsn: str,
    stop_command: list[str],
    start_command: list[str],
) -> dict[str, Any]:
    migrations_dir = PROJECT_ROOT / "backend" / "migrations"
    with tempfile.TemporaryDirectory(prefix="ragclaw-external-jsonl-") as temp_dir:
        dual = DualWriteRunTraceRepository(
            jsonl_repository=JsonlRunTraceRepository(Path(temp_dir) / "runs"),
            postgres_repository=PostgresRunTraceRepository(postgres_dsn, migrations_dir=migrations_dir),
        )
        session_repository = PostgresSessionRepository(postgres_dsn, migrations_dir=migrations_dir)
        dual.postgres_repository.reset_all()
        metadata = RunMetadata(
            run_id="external-postgres-run",
            session_id="external-session",
            user_message="postgres drill",
            source="external_infra_matrix",
            started_at=datetime.now(UTC).isoformat(),
            orchestration_engine="langgraph",
        )
        dual.create_run(metadata)
        dual.append_event(
            HarnessEvent(
                event_id="evt-1",
                run_id=metadata.run_id,
                name="run.started",
                ts=datetime.now(UTC).isoformat(),
                payload={"session_id": metadata.session_id},
            )
        )
        session_repository.create_session("External Session Repository Drill")
        session_repository.save_message("external-session", "user", "postgres drill message", message_id="msg-1", run_id=metadata.run_id)

        stop_code, stop_stdout, stop_stderr = _run_command(stop_command, timeout=60.0, capture_output=False)
        if stop_code != 0:
            raise RuntimeError(f"postgres stop command failed ({stop_code}): {stop_stderr or stop_stdout}")

        retry_error = ""
        retry_failed = False
        event_two = HarnessEvent(
            event_id="evt-2",
            run_id=metadata.run_id,
            name="route.decided",
            ts=datetime.now(UTC).isoformat(),
            payload={"intent": "direct_answer", "needs_tools": False, "needs_retrieval": False},
        )
        try:
            dual.append_event(event_two)
        except Exception as exc:
            retry_failed = True
            retry_error = str(exc)

        start_code, start_stdout, start_stderr = _run_command(start_command, timeout=120.0, capture_output=False)
        if start_code != 0:
            raise RuntimeError(f"postgres start command failed ({start_code}): {start_stderr or start_stdout}")
        _wait_for_postgres(postgres_dsn)

        dual.append_event(event_two)
        dual.append_event(
            HarnessEvent(
                event_id="evt-3",
                run_id=metadata.run_id,
                name="answer.completed",
                ts=datetime.now(UTC).isoformat(),
                payload={"content": "external drill complete", "final": True},
            )
        )
        dual.finalize_run(
            metadata.run_id,
            RunOutcome(
                status="completed",
                final_answer="external drill complete",
                route_intent="direct_answer",
                completed_at=datetime.now(UTC).isoformat(),
                orchestration_engine="langgraph",
            ),
        )

        session_repository.save_message("external-session", "assistant", "postgres drill answer", message_id="msg-2", run_id=metadata.run_id)
        record = session_repository.load_session_record("external-session")
        trace = dual.postgres_repository.read_trace(metadata.run_id)
        parity = dual.parity_report(metadata.run_id) or {}
        event_ids = [str(item.get("event_id", "") or "") for item in trace.get("events", [])]
        monotonic_seq = len(event_ids) == len(set(event_ids)) == int(trace.get("event_count", 0) or 0)
        parity_mismatch = 0 if parity.get("ordering_match") and parity.get("jsonl_checksum") == parity.get("postgres_checksum") else 1
        return {
            "name": "postgres_retry_drill",
            "status": "passed" if retry_failed and monotonic_seq and parity_mismatch == 0 else "failed",
            "initial_disconnect_observed": retry_failed,
            "initial_disconnect_error": retry_error,
            "restart_commands": {
                "stop": stop_command,
                "start": start_command,
                "stop_exit_code": stop_code,
                "start_exit_code": start_code,
            },
            "event_count": int(trace.get("event_count", 0) or 0),
            "event_ids": event_ids,
            "event_seq_monotonic": monotonic_seq,
            "dual_write_parity_mismatch_count": parity_mismatch,
            "session_message_count": len(record.get("messages", [])),
            "retry_success": True,
        }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real external Redis/Postgres drills when infra is available.")
    parser.add_argument("--output", required=True, help="Path to external_infra_matrix.json")
    parser.add_argument("--mode", choices=("auto", "docker", "direct"), default="auto")
    parser.add_argument("--require-external-infra", action="store_true")
    parser.add_argument("--postgres-dsn", default=str(os.getenv("RAGCLAW_POSTGRES_DSN") or os.getenv("POSTGRES_DSN") or ""))
    parser.add_argument("--allow-local-postgres-restart", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_path = Path(args.output)
    output_dir = output_path.parent
    machine_capabilities_path = output_dir / "machine_capabilities.json"
    capability_report = write_machine_capabilities(machine_capabilities_path, postgres_dsn=args.postgres_dsn or None)
    drills: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    external_available = bool(capability_report.get("modes", {}).get("external_infra", {}).get("available", False))
    if args.require_external_infra and not external_available:
        blocked_payload = attach_execution_metadata(
            {
                "started_at": datetime.now(UTC).isoformat(),
                "completed_at": datetime.now(UTC).isoformat(),
                "mode": args.mode,
                "machine_capabilities_path": str(machine_capabilities_path),
                "machine_capabilities": capability_report,
                "drills": [],
                "blocked": [
                    {
                        "name": "external_infra_matrix",
                        "reason": "external infrastructure is required but unavailable on this machine",
                    }
                ],
            },
            config={"mode": args.mode, "require_external_infra": True},
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(blocked_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(output_path))
        return 2

    started_at = datetime.now(UTC).isoformat()
    postgres_dsn = str(args.postgres_dsn or "").strip()
    try:
        redis_provider = "blocked"
        postgres_provider = "blocked"
        docker_available = bool(capability_report.get("capabilities", {}).get("docker", {}).get("available", False))
        redis_server_available = bool(capability_report.get("capabilities", {}).get("redis_server", {}).get("available", False))
        postgres_direct_available = bool(capability_report.get("capabilities", {}).get("postgres_dsn", {}).get("available", False)) and bool(
            capability_report.get("capabilities", {}).get("postgres_local_scripts", {}).get("available", False)
        )

        if args.mode in {"auto", "docker"} and docker_available:
            redis_provider = "docker"
            postgres_provider = "docker"
        elif args.mode in {"auto", "direct"}:
            if redis_server_available:
                redis_provider = "direct"
            if postgres_direct_available and args.allow_local_postgres_restart:
                postgres_provider = "direct"

        with ExitStack() as stack:
            redis_url = ""
            stop_redis = None
            start_redis = None
            if redis_provider == "docker":
                redis_port = _find_free_port()
                redis_service = _DockerService(
                    image="redis:7-alpine",
                    name=f"ragclaw-redis-{int(time.time())}",
                    ports={redis_port: 6379},
                    env={},
                )
                redis_service.start()
                stack.callback(redis_service.cleanup)
                redis_url = f"redis://127.0.0.1:{redis_port}/0"
                stop_redis = redis_service.stop
                start_redis = redis_service.restart
            elif redis_provider == "direct":
                redis_port = _find_free_port()
                redis_service = _DirectRedisService(
                    executable=str(capability_report["capabilities"]["redis_server"]["path"]),
                    port=redis_port,
                )
                redis_service.start()
                stack.callback(redis_service.stop)
                redis_url = f"redis://127.0.0.1:{redis_port}/0"
                stop_redis = redis_service.stop
                start_redis = redis_service.start
            else:
                blocked.append(
                    {
                        "name": "redis_drills",
                        "reason": capability_report.get("drills", {}).get("redis_restart_drill", {}).get("blocked_reason", "redis external drill unavailable"),
                    }
                )

            stop_postgres_command = _default_postgres_stop_command()
            start_postgres_command = _default_postgres_start_command()
            if postgres_provider == "docker":
                postgres_port = _find_free_port()
                postgres_service = _DockerService(
                    image="postgres:16-alpine",
                    name=f"ragclaw-postgres-{int(time.time())}",
                    ports={postgres_port: 5432},
                    env={"POSTGRES_HOST_AUTH_METHOD": "trust"},
                )
                postgres_service.start()
                stack.callback(postgres_service.cleanup)
                postgres_dsn = f"postgresql://postgres@127.0.0.1:{postgres_port}/postgres"
                _wait_for_postgres(postgres_dsn, timeout_seconds=45.0)
                stop_postgres_command = ["docker", "stop", postgres_service.name]
                start_postgres_command = ["docker", "start", postgres_service.name]
            elif postgres_provider != "direct":
                blocked.append(
                    {
                        "name": "postgres_drills",
                        "reason": capability_report.get("drills", {}).get("postgres_transient_disconnect_drill", {}).get(
                            "blocked_reason",
                            "postgres external drill unavailable",
                        )
                        if args.allow_local_postgres_restart
                        else "local postgres restart drill requires --allow-local-postgres-restart",
                    }
                )

            if redis_url and stop_redis and start_redis:
                drills.append(asyncio.run(_redis_lease_expiry_drill(redis_url)))
                drills.append(asyncio.run(_redis_restart_drill(redis_url, stop_redis, start_redis)))

            if postgres_provider in {"docker", "direct"} and postgres_dsn:
                drill = _postgres_retry_drill(
                    postgres_dsn=postgres_dsn,
                    stop_command=stop_postgres_command,
                    start_command=start_postgres_command,
                )
                drills.append(drill)
                (output_dir / "postgres_retry_drill.json").write_text(json.dumps(drill, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        payload = attach_execution_metadata(
            {
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
                "mode": args.mode,
                "machine_capabilities_path": str(machine_capabilities_path),
                "machine_capabilities": capability_report,
                "drills": drills,
                "blocked": blocked,
                "status": "failed",
                "error": str(exc),
            },
            config={
                "mode": args.mode,
                "require_external_infra": args.require_external_infra,
                "allow_local_postgres_restart": args.allow_local_postgres_restart,
                "postgres_dsn_configured": bool(postgres_dsn),
            },
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(output_path))
        print(json.dumps({"error": str(exc), "drills": drills, "blocked": blocked}, ensure_ascii=False, indent=2))
        return 1

    payload = attach_execution_metadata(
        {
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "mode": args.mode,
            "machine_capabilities_path": str(machine_capabilities_path),
            "machine_capabilities": capability_report,
            "drills": drills,
            "blocked": blocked,
        },
        config={
            "mode": args.mode,
            "require_external_infra": args.require_external_infra,
            "allow_local_postgres_restart": args.allow_local_postgres_restart,
            "postgres_dsn_configured": bool(postgres_dsn),
        },
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output_path))
    print(json.dumps({"drills": drills, "blocked": blocked}, ensure_ascii=False, indent=2))
    return 0 if all(item.get("status") != "failed" for item in drills) else 1


if __name__ == "__main__":
    raise SystemExit(main())
