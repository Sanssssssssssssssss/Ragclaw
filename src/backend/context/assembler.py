from __future__ import annotations

from typing import Any

from src.backend.context.artifact_selector import ArtifactSelector
from src.backend.context.budget import DEFAULT_EXCLUDED_FROM_PROMPT, budget_for_path, trim_messages_to_budget, trim_text_to_budget
from src.backend.context.models import ContextAssembly, ContextPathKind
from src.backend.runtime.token_utils import count_tokens


def _format_working_memory_block(memory: dict[str, Any], *, fields: tuple[str, ...]) -> str:
    lines = ["[Working memory]"]
    for field_name in fields:
        value = memory.get(field_name)
        if value in (None, "", [], ()):
            continue
        if isinstance(value, list):
            lines.append(f"{field_name}: " + "; ".join(str(item) for item in value if str(item).strip()))
        else:
            lines.append(f"{field_name}: {value}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_summary_block(summary: dict[str, Any]) -> str:
    lines = ["[Episodic summary]"]
    for field_name in (
        "key_facts",
        "completed_subtasks",
        "rejected_paths",
        "important_decisions",
        "important_artifacts",
        "open_loops",
    ):
        value = summary.get(field_name)
        if not value:
            continue
        if isinstance(value, list):
            lines.append(f"{field_name}: " + "; ".join(str(item) for item in value if str(item).strip()))
        else:
            lines.append(f"{field_name}: {value}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _join_block(label: str, items: list[str]) -> str:
    if not items:
        return ""
    lines = [f"[{label}]"]
    for index, item in enumerate(items, start=1):
        normalized = str(item or "").strip()
        if normalized:
            lines.append(f"{index}. {normalized}")
    return "\n".join(lines) if len(lines) > 1 else ""


class ContextAssembler:
    def __init__(self) -> None:
        self._artifact_selector = ArtifactSelector()

    def assemble(self, *, path_kind: ContextPathKind, state: dict[str, Any]) -> ContextAssembly:
        effective_path = self._effective_path_kind(path_kind, state)
        budget = budget_for_path(effective_path)
        history = trim_messages_to_budget(self._history_source(state), budget.recent_history)

        working_memory = dict(state.get("working_memory", {}) or {})
        episodic_summary = dict(state.get("episodic_summary", {}) or {})

        working_memory_fields = self._working_memory_fields(effective_path)
        working_memory_block = trim_text_to_budget(
            _format_working_memory_block(working_memory, fields=working_memory_fields),
            budget.working_memory,
        )
        summary_block = trim_text_to_budget(_format_summary_block(episodic_summary), budget.summary)
        artifacts_block = trim_text_to_budget(
            _join_block(
                "Capability outputs",
                self._artifact_selector.select_capability_outputs(state, path_kind=effective_path),
            ),
            budget.artifacts,
        )
        retrieval_block = trim_text_to_budget(
            _join_block(
                "Retrieval evidence",
                self._artifact_selector.select_retrieval_evidence(state, path_kind=effective_path),
            ),
            budget.retrieval_evidence,
        )
        extra_instructions = tuple(
            block
            for block in (working_memory_block, summary_block, artifacts_block, retrieval_block)
            if block
        )
        budget_used = {
            "recent_history": self._message_tokens(history),
            "working_memory": count_tokens(working_memory_block),
            "summary": count_tokens(summary_block),
            "artifacts": count_tokens(artifacts_block),
            "retrieval_evidence": count_tokens(retrieval_block),
        }
        return ContextAssembly(
            path_kind=effective_path,
            history_messages=tuple(history),
            extra_instructions=extra_instructions,
            working_memory_block=working_memory_block,
            summary_block=summary_block,
            artifacts_block=artifacts_block,
            retrieval_block=retrieval_block,
            budget=budget,
            budget_used=budget_used,
            excluded_from_prompt=DEFAULT_EXCLUDED_FROM_PROMPT,
        )

    def _effective_path_kind(self, path_kind: ContextPathKind, state: dict[str, Any]) -> ContextPathKind:
        checkpoint_meta = dict(state.get("checkpoint_meta", {}) or {})
        run_status = str(checkpoint_meta.get("run_status", "") or "")
        if run_status in {"resumed", "restoring", "interrupted"} or state.get("interrupt_request"):
            return "resumed_hitl"
        return path_kind

    def _history_source(self, state: dict[str, Any]) -> list[dict[str, str]]:
        history = list(state.get("history", []) or [])
        normalized: list[dict[str, str]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "")
            content = str(item.get("content", "") or "").strip()
            if role in {"user", "assistant"} and content:
                normalized.append({"role": role, "content": content})
        return normalized

    def _working_memory_fields(self, path_kind: ContextPathKind) -> tuple[str, ...]:
        if path_kind == "knowledge_qa":
            return (
                "current_goal",
                "latest_user_intent",
                "active_constraints",
                "active_entities",
                "latest_retrieval_summary",
                "unresolved_items",
            )
        if path_kind == "capability":
            return (
                "current_goal",
                "latest_user_intent",
                "active_constraints",
                "active_artifacts",
                "latest_capability_results",
                "latest_retrieval_summary",
                "unresolved_items",
            )
        if path_kind == "resumed_hitl":
            return (
                "current_goal",
                "latest_user_intent",
                "active_constraints",
                "active_entities",
                "active_artifacts",
                "latest_capability_results",
                "latest_retrieval_summary",
                "unresolved_items",
            )
        return (
            "current_goal",
            "latest_user_intent",
            "active_constraints",
            "active_entities",
            "latest_retrieval_summary",
            "unresolved_items",
        )

    def _message_tokens(self, messages: list[dict[str, str]]) -> int:
        return count_tokens("\n\n".join(f"{item['role']}: {item['content']}" for item in messages))
