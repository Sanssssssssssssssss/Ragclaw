from __future__ import annotations

import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent


def _git_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return ""
    return str(completed.stdout or "").strip()


def benchmark_execution_metadata(*, config: dict[str, Any] | None = None) -> dict[str, Any]:
    observed_env = {
        name: str(os.getenv(name) or "")
        for name in (
            "RAGCLAW_SESSION_BACKEND",
            "RAGCLAW_TRACE_BACKEND",
            "RAGCLAW_QUEUE_BACKEND",
            "RAGCLAW_HITL_BACKEND",
            "RAGCLAW_REDIS_URL",
            "RAGCLAW_POSTGRES_DSN",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "RAGCLAW_OTEL_ENABLED",
            "RAGCLAW_OTEL_CONSOLE_EXPORTER",
        )
        if str(os.getenv(name) or "").strip()
    }
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "cwd": str(PROJECT_ROOT),
        "config": dict(config or {}),
        "environment": observed_env,
    }


def attach_execution_metadata(payload: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["execution_metadata"] = benchmark_execution_metadata(config=config)
    return enriched


__all__ = ["attach_execution_metadata", "benchmark_execution_metadata"]
