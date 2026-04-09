from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from src.backend.context.models import EpisodicSummary, WorkingMemory


_PREFERENCE_PATTERNS = (
    re.compile(r"\b(prefer|always|never|only|must|avoid|don't|do not)\b", re.IGNORECASE),
    re.compile(r"(偏好|总是|不要|只用|必须|避免|禁用)"),
)


def project_namespace(base_dir: Path | None) -> str:
    return f"project:{(base_dir.name if base_dir is not None else 'default').lower()}"


def user_namespace(user_id: str | None = None) -> str:
    return f"user:{(user_id or 'default').strip() or 'default'}"


def thread_namespace(thread_id: str) -> str:
    return f"thread:{thread_id}"


def fingerprint_for(kind: str, namespace: str, content: str, tags: list[str] | tuple[str, ...]) -> str:
    payload = f"{kind}|{namespace}|{content.strip()}|{','.join(sorted(str(item) for item in tags))}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def semantic_query_for(state: dict[str, Any], working_memory: dict[str, Any]) -> str:
    parts = [
        str(working_memory.get("current_goal", "") or ""),
        str(working_memory.get("latest_user_intent", "") or ""),
        " ".join(str(item) for item in working_memory.get("active_entities", []) or []),
        " ".join(str(item) for item in working_memory.get("active_artifacts", []) or []),
    ]
    return " ".join(part for part in parts if part).strip()


def procedural_query_for(state: dict[str, Any], working_memory: dict[str, Any]) -> str:
    parts = [
        str(working_memory.get("latest_user_intent", "") or ""),
        " ".join(str(item) for item in working_memory.get("active_constraints", []) or []),
        " ".join(str(item) for item in state.get("selected_capabilities", []) or []),
    ]
    return " ".join(part for part in parts if part).strip()


def promotion_candidates(
    *,
    state: dict[str, Any],
    working_memory: WorkingMemory,
    episodic_summary: EpisodicSummary,
    base_dir: Path | None,
    updated_at: str,
) -> dict[str, list[dict[str, Any]]]:
    semantic: list[dict[str, Any]] = []
    procedural: list[dict[str, Any]] = []

    for fact in episodic_summary.key_facts[:2]:
        content = str(fact).strip()
        if len(content) < 24:
            continue
        semantic.append(
            {
                "namespace": thread_namespace(working_memory.thread_id),
                "title": "Session fact",
                "content": content,
                "summary": content[:120],
                "tags": ["summary", "thread"],
                "metadata": {"thread_id": working_memory.thread_id, "updated_at": updated_at},
                "source": "episodic_summary",
            }
        )

    for artifact in episodic_summary.important_artifacts[:3]:
        path = str(artifact).strip()
        if "/" not in path and "\\" not in path:
            continue
        semantic.append(
            {
                "namespace": project_namespace(base_dir),
                "title": "Project artifact",
                "content": path,
                "summary": path,
                "tags": ["artifact", "project"],
                "metadata": {"thread_id": working_memory.thread_id},
                "source": "artifact_selector",
            }
        )

    for constraint in working_memory.active_constraints:
        text = str(constraint).strip()
        if not text or not any(pattern.search(text) for pattern in _PREFERENCE_PATTERNS):
            continue
        procedural.append(
            {
                "namespace": user_namespace(),
                "title": "User preference",
                "content": text,
                "summary": text[:120],
                "tags": ["user_preference", "constraint"],
                "metadata": {"thread_id": working_memory.thread_id},
                "source": "working_memory",
            }
        )

    for decision in episodic_summary.important_decisions[:3]:
        text = str(decision).strip()
        if not text:
            continue
        procedural.append(
            {
                "namespace": project_namespace(base_dir),
                "title": "Project rule",
                "content": text,
                "summary": text[:120],
                "tags": ["project_rule"],
                "metadata": {"thread_id": working_memory.thread_id},
                "source": "episodic_summary",
            }
        )

    return {"semantic": semantic, "procedural": procedural}
