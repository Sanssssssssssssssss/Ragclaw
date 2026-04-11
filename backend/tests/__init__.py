from __future__ import annotations

import os


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


if not _truthy(os.getenv("RAGCLAW_TEST_ENABLE_LANGSMITH")):
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ.setdefault("LANGSMITH_PROJECT", "Ragclaw Tests")
    os.environ.setdefault("LANGCHAIN_PROJECT", "Ragclaw Tests")
