from __future__ import annotations

import json
import multiprocessing
import os
import platform
import shutil
import subprocess
import sys
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

from benchmarks.execution_metadata import benchmark_execution_metadata


def _probe_spawn_worker(queue) -> None:
    queue.put({"pid": os.getpid()})


@dataclass(frozen=True)
class CapabilityCheck:
    available: bool
    detail: str
    path: str = ""
    command: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "detail": self.detail,
            "path": self.path,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def _command_check(command: list[str], *, executable: str | None = None, timeout: float = 8.0) -> CapabilityCheck:
    resolved = shutil.which(executable or command[0])
    if not resolved:
        return CapabilityCheck(
            available=False,
            detail=f"{executable or command[0]} not found on PATH",
            command=" ".join(command),
        )
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return CapabilityCheck(
            available=False,
            detail=f"command failed: {exc}",
            path=resolved,
            command=" ".join(command),
        )
    stdout = str(completed.stdout or "").strip()
    stderr = str(completed.stderr or "").strip()
    available = completed.returncode == 0
    detail = stdout.splitlines()[0] if stdout else (stderr.splitlines()[0] if stderr else f"exit code {completed.returncode}")
    return CapabilityCheck(
        available=available,
        detail=detail,
        path=resolved,
        command=" ".join(command),
        exit_code=int(completed.returncode),
        stdout=stdout,
        stderr=stderr,
    )


def _module_check(module_name: str) -> CapabilityCheck:
    try:
        __import__(module_name)
        return CapabilityCheck(available=True, detail=f"{module_name} import ok")
    except Exception as exc:
        return CapabilityCheck(available=False, detail=f"{module_name} import failed: {exc}")


def _postgres_dsn_check(postgres_dsn: str | None) -> CapabilityCheck:
    dsn = str(postgres_dsn or "").strip()
    if not dsn:
        return CapabilityCheck(available=False, detail="postgres dsn not configured")
    try:
        import psycopg

        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
        return CapabilityCheck(available=bool(row), detail="postgres dsn reachable", command="postgres dsn probe")
    except Exception as exc:
        return CapabilityCheck(available=False, detail=f"postgres dsn probe failed: {exc}", command="postgres dsn probe")


def _multiprocess_spawn_check(timeout: float = 5.0) -> CapabilityCheck:
    try:
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        process = ctx.Process(target=_probe_spawn_worker, args=(queue,))
        process.start()
        process.join(timeout=timeout)
        payload = queue.get(timeout=timeout) if not queue.empty() else {}
        if process.exitcode != 0:
            return CapabilityCheck(available=False, detail=f"spawn worker exited with {process.exitcode}")
        return CapabilityCheck(available=True, detail=f"spawn worker pid={payload.get('pid', '')}")
    except Exception as exc:
        return CapabilityCheck(available=False, detail=f"spawn worker failed: {exc}")


def detect_machine_capabilities(*, postgres_dsn: str | None = None) -> dict[str, Any]:
    postgres_start_script = PROJECT_ROOT / "backend" / "scripts" / "dev" / "start-postgres-local.ps1"
    postgres_stop_script = PROJECT_ROOT / "backend" / "scripts" / "dev" / "stop-postgres-local.ps1"
    python_modules = {
        name: _module_check(name).to_dict()
        for name in (
            "psycopg",
            "redis",
            "fakeredis",
            "prometheus_client",
            "opentelemetry.sdk",
        )
    }

    capabilities = {
        "docker": _command_check(["docker", "version"], executable="docker").to_dict(),
        "redis_server": _command_check(["redis-server", "--version"], executable="redis-server").to_dict(),
        "postgres_dsn": _postgres_dsn_check(postgres_dsn).to_dict(),
        "python_modules": {
            "available": all(item.get("available", False) for item in python_modules.values()),
            "modules": python_modules,
        },
        "multiprocessing_spawn": _multiprocess_spawn_check().to_dict(),
        "postgres_local_scripts": {
            "available": postgres_start_script.exists() and postgres_stop_script.exists(),
            "start_script": str(postgres_start_script),
            "stop_script": str(postgres_stop_script),
        },
    }

    docker_available = bool(capabilities["docker"]["available"])
    redis_direct_available = bool(capabilities["redis_server"]["available"])
    postgres_direct_available = bool(capabilities["postgres_dsn"]["available"]) and bool(capabilities["postgres_local_scripts"]["available"])
    external_infra_available = docker_available or redis_direct_available or postgres_direct_available

    drills = {
        "redis_restart_drill": {
            "runnable": docker_available or redis_direct_available,
            "provider": "docker" if docker_available else "direct" if redis_direct_available else "blocked",
            "blocked_reason": ""
            if docker_available or redis_direct_available
            else "requires docker or redis-server for a real external Redis drill",
        },
        "redis_lease_expiry_drill": {
            "runnable": docker_available or redis_direct_available,
            "provider": "docker" if docker_available else "direct" if redis_direct_available else "blocked",
            "blocked_reason": ""
            if docker_available or redis_direct_available
            else "requires docker or redis-server for a real external Redis drill",
        },
        "postgres_transient_disconnect_drill": {
            "runnable": docker_available or postgres_direct_available,
            "provider": "docker" if docker_available else "direct" if postgres_direct_available else "blocked",
            "blocked_reason": ""
            if docker_available or postgres_direct_available
            else "requires docker or a reachable Postgres DSN plus local start/stop scripts",
        },
        "postgres_restart_reconnect_drill": {
            "runnable": docker_available or postgres_direct_available,
            "provider": "docker" if docker_available else "direct" if postgres_direct_available else "blocked",
            "blocked_reason": ""
            if docker_available or postgres_direct_available
            else "requires docker or a reachable Postgres DSN plus local start/stop scripts",
        },
    }

    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "execution_metadata": benchmark_execution_metadata(config={"postgres_dsn_configured": bool(str(postgres_dsn or "").strip())}),
        "capabilities": capabilities,
        "modes": {
            "local_only": {"available": True, "detail": "filesystem/inmemory/jsonl mode is always available"},
            "external_infra": {
                "available": external_infra_available,
                "detail": "docker or direct Redis/Postgres controls available" if external_infra_available else "external infra controls missing on this machine",
            },
        },
        "drills": drills,
    }


def write_machine_capabilities(
    output_path: Path,
    *,
    postgres_dsn: str | None = None,
) -> dict[str, Any]:
    payload = detect_machine_capabilities(postgres_dsn=postgres_dsn)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


__all__ = ["CapabilityCheck", "detect_machine_capabilities", "write_machine_capabilities"]
