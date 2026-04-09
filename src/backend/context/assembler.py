from __future__ import annotations

from pathlib import Path
from typing import Any

from src.backend.context.artifact_selector import ArtifactSelector
from src.backend.context.budget import (
    DEFAULT_EXCLUDED_FROM_PROMPT,
    budget_for_path,
    trim_messages_to_budget,
    trim_text_to_budget,
)
from src.backend.context.models import (
    ContextAssembly,
    ContextAssemblyDecision,
    ContextEnvelope,
    ContextPathKind,
    StoredMemory,
)
from src.backend.context.policies import procedural_query_for, project_namespace, semantic_query_for, thread_namespace, user_namespace
from src.backend.context.procedural_memory import procedural_memory
from src.backend.context.semantic_memory import semantic_memory
from src.backend.context.store import context_store
from src.backend.runtime.token_utils import count_tokens


def _format_list_block(label: str, items: list[str]) -> str:
    if not items:
        return ""
    lines = [f"[{label}]"]
    for index, item in enumerate(items, start=1):
        normalized = str(item or "").strip()
        if normalized:
            lines.append(f"{index}. {normalized}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_working_memory_block(memory: dict[str, Any], *, fields: tuple[str, ...]) -> str:
    lines = ["[Working memory]"]
    for field_name in fields:
        value = memory.get(field_name)
        if value in (None, "", [], ()):
            continue
        if isinstance(value, list):
            normalized = "; ".join(str(item) for item in value if str(item).strip())
            if normalized:
                lines.append(f"{field_name}: {normalized}")
        else:
            lines.append(f"{field_name}: {value}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_episodic_block(summary: dict[str, Any]) -> str:
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
            normalized = "; ".join(str(item) for item in value if str(item).strip())
            if normalized:
                lines.append(f"{field_name}: {normalized}")
        else:
            lines.append(f"{field_name}: {value}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_memory_block(label: str, items: list[StoredMemory]) -> str:
    if not items:
        return ""
    lines = [f"[{label}]"]
    for item in items:
        summary = str(item.summary or "").strip()
        content = summary or str(item.content or "").strip()
        header = f"{item.title} ({item.namespace})".strip()
        if header:
            lines.append(f"- {header}")
        if content:
            lines.append(f"  {content}")
    return "\n".join(lines)


def _history_ids(source_messages: list[dict[str, str]], selected_messages: list[dict[str, str]]) -> tuple[str, ...]:
    if not selected_messages:
        return ()
    selected_pairs = [(item.get("role", ""), item.get("content", "")) for item in selected_messages]
    ids: list[str] = []
    for index, item in enumerate(source_messages):
        pair = (item.get("role", ""), item.get("content", ""))
        if pair in selected_pairs:
            ids.append(f"history:{index}")
    return tuple(ids)


class ContextAssembler:
    def __init__(self, *, base_dir: Path | None = None) -> None:
        self._artifact_selector = ArtifactSelector()
        self._base_dir = Path(base_dir) if base_dir is not None else None

    def assemble(
        self,
        *,
        path_kind: ContextPathKind,
        state: dict[str, Any],
        call_site: str = "",
    ) -> ContextAssembly:
        effective_path = self._effective_path_kind(path_kind, state)
        budget = budget_for_path(effective_path)
        source_history = self._history_source(state)
        history_messages = trim_messages_to_budget(source_history, budget.recent_history)

        thread_id = str(state.get("thread_id", "") or state.get("session_id", "") or state.get("run_id", "") or "")
        working_memory = self._working_memory_payload(state, thread_id=thread_id)
        episodic_summary = self._episodic_payload(state, thread_id=thread_id)

        semantic_items, procedural_items = self._memory_hits(state, working_memory, thread_id=thread_id)

        working_memory_block = trim_text_to_budget(
            _format_working_memory_block(working_memory, fields=self._working_memory_fields(effective_path)),
            budget.working_memory,
        )
        episodic_block = trim_text_to_budget(
            _format_episodic_block(episodic_summary),
            budget.episodic_summary,
        )
        semantic_block = trim_text_to_budget(
            _format_memory_block("Semantic memory", semantic_items),
            budget.semantic_memory,
        )
        procedural_block = trim_text_to_budget(
            _format_memory_block("Procedural memory", procedural_items),
            budget.procedural_memory,
        )
        artifacts = self._artifact_selector.select_capability_outputs(state, path_kind=effective_path)
        artifacts_block = trim_text_to_budget(
            _format_list_block("Capability outputs", artifacts),
            budget.artifacts,
        )
        evidence = self._artifact_selector.select_retrieval_evidence(state, path_kind=effective_path)
        retrieval_block = trim_text_to_budget(
            _format_list_block("Retrieval evidence", evidence),
            budget.retrieval_evidence,
        )

        budget_used = {
            "recent_history": self._message_tokens(history_messages),
            "working_memory": count_tokens(working_memory_block),
            "episodic_summary": count_tokens(episodic_block),
            "semantic_memory": count_tokens(semantic_block),
            "procedural_memory": count_tokens(procedural_block),
            "artifacts": count_tokens(artifacts_block),
            "retrieval_evidence": count_tokens(retrieval_block),
            "answer_reserve": budget.answer_reserve,
        }

        envelope = ContextEnvelope(
            system_block=self._system_block(effective_path),
            history_block=self._history_block(history_messages),
            working_memory_block=working_memory_block,
            episodic_block=episodic_block,
            semantic_block=semantic_block,
            procedural_block=procedural_block,
            artifact_block=artifacts_block,
            evidence_block=retrieval_block,
            budget_report=dict(budget_used),
        )

        dropped_items = list(self._dropped_history_ids(source_history, history_messages))
        truncation_reasons: list[str] = []
        if len(history_messages) < len(source_history):
            truncation_reasons.append("recent_history budget")
        if semantic_items and not semantic_block:
            truncation_reasons.append("semantic memory budget")
        if procedural_items and not procedural_block:
            truncation_reasons.append("procedural memory budget")
        if artifacts and not artifacts_block:
            truncation_reasons.append("artifact budget")
        if evidence and not retrieval_block:
            truncation_reasons.append("retrieval evidence budget")

        decision = ContextAssemblyDecision(
            path_type=effective_path,
            selected_history_ids=_history_ids(source_history, history_messages),
            selected_memory_ids=tuple(
                [f"working:{thread_id}", f"episodic:{thread_id}"]
                + [item.memory_id for item in semantic_items]
                + [item.memory_id for item in procedural_items]
            ),
            selected_artifact_ids=tuple(self._selected_artifact_ids(state, artifacts)),
            selected_evidence_ids=tuple(self._selected_evidence_ids(state, evidence)),
            dropped_items=tuple(dropped_items),
            truncation_reason=", ".join(truncation_reasons),
        )

        assembly = ContextAssembly(
            path_kind=effective_path,
            history_messages=tuple(history_messages),
            envelope=envelope,
            decision=decision,
            extra_instructions=tuple(
                block
                for block in (
                    envelope.system_block,
                    envelope.working_memory_block,
                    envelope.episodic_block,
                    envelope.semantic_block,
                    envelope.procedural_block,
                    envelope.artifact_block,
                    envelope.evidence_block,
                )
                if block
            ),
            working_memory_block=working_memory_block,
            episodic_block=episodic_block,
            semantic_block=semantic_block,
            procedural_block=procedural_block,
            artifacts_block=artifacts_block,
            retrieval_block=retrieval_block,
            budget=budget,
            budget_used=budget_used,
            excluded_from_prompt=DEFAULT_EXCLUDED_FROM_PROMPT,
        )
        self._record_assembly(state=state, call_site=call_site or effective_path, assembly=assembly)
        return assembly

    def _effective_path_kind(self, path_kind: ContextPathKind, state: dict[str, Any]) -> ContextPathKind:
        checkpoint_meta = dict(state.get("checkpoint_meta", {}) or {})
        run_status = str(checkpoint_meta.get("run_status", "") or "")
        if path_kind == "recovery_path":
            return "recovery_path"
        if run_status in {"resumed", "restoring", "interrupted"} or state.get("interrupt_request"):
            return "resumed_hitl"
        return "capability_path" if path_kind == "capability_path" else path_kind

    def _history_source(self, state: dict[str, Any]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for item in list(state.get("history", []) or []):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip()
            content = str(item.get("content", "") or "").strip()
            if role in {"user", "assistant"} and content:
                normalized.append({"role": role, "content": content})
        return normalized

    def _history_block(self, history_messages: list[dict[str, str]]) -> str:
        if not history_messages:
            return ""
        lines = ["[Recent history]"]
        for item in history_messages:
            lines.append(f"{item['role']}: {item['content']}")
        return "\n".join(lines)

    def _working_memory_payload(self, state: dict[str, Any], *, thread_id: str) -> dict[str, Any]:
        payload = dict(state.get("working_memory", {}) or {})
        if payload:
            return payload
        try:
            snapshot = context_store.get_thread_snapshot(thread_id=thread_id) if thread_id else None
        except Exception:
            snapshot = None
        return dict(snapshot.working_memory) if snapshot is not None else {}

    def _episodic_payload(self, state: dict[str, Any], *, thread_id: str) -> dict[str, Any]:
        payload = dict(state.get("episodic_summary", {}) or {})
        if payload:
            return payload
        try:
            snapshot = context_store.get_thread_snapshot(thread_id=thread_id) if thread_id else None
        except Exception:
            snapshot = None
        return dict(snapshot.episodic_summary) if snapshot is not None else {}

    def _memory_hits(
        self,
        state: dict[str, Any],
        working_memory: dict[str, Any],
        *,
        thread_id: str,
    ) -> tuple[list[StoredMemory], list[StoredMemory]]:
        namespaces = self._memory_namespaces(thread_id)
        try:
            semantic_items = semantic_memory.search(
                namespaces=namespaces,
                query=semantic_query_for(state, working_memory),
                limit=4,
            )
            procedural_items = procedural_memory.search(
                namespaces=namespaces,
                query=procedural_query_for(state, working_memory),
                limit=4,
            )
        except Exception:
            return [], []
        return semantic_items, procedural_items

    def _memory_namespaces(self, thread_id: str) -> tuple[str, ...]:
        namespaces: list[str] = [user_namespace()]
        namespaces.append(project_namespace(self._base_dir))
        if thread_id:
            namespaces.append(thread_namespace(thread_id))
        return tuple(dict.fromkeys(namespace for namespace in namespaces if namespace))

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
        if path_kind in {"capability_path", "recovery_path"}:
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

    def _system_block(self, path_kind: ContextPathKind) -> str:
        if path_kind == "knowledge_qa":
            return "[Context policy]\nUse retrieved evidence and summaries only. Do not fabricate unsupported file facts."
        if path_kind == "capability_path":
            return "[Context policy]\nPrefer the latest grounded capability results and active constraints. Do not restate raw audit or trace data."
        if path_kind == "resumed_hitl":
            return "[Context policy]\nThis run resumed from checkpoint/HITL. Use summaries and approved inputs, not raw checkpoint dumps."
        if path_kind == "recovery_path":
            return "[Context policy]\nThis run is recovering from a capability failure. Use only the recovery summary and surviving evidence."
        return "[Context policy]\nAnswer directly using recent history plus compact working and episodic memory."

    def _selected_artifact_ids(self, state: dict[str, Any], artifacts: list[str]) -> list[str]:
        selected_ids: list[str] = []
        recent_results = list(state.get("capability_results", []) or [])
        for item in recent_results[-len(artifacts) :]:
            if isinstance(item, dict):
                capability_id = str(item.get("capability_id", "") or "").strip()
                if capability_id:
                    selected_ids.append(capability_id)
        return selected_ids

    def _selected_evidence_ids(self, state: dict[str, Any], evidence: list[str]) -> list[str]:
        selected_ids: list[str] = []
        memory_retrieval = list(state.get("memory_retrieval", []) or [])
        for item in memory_retrieval[:2]:
            if isinstance(item, dict):
                source = str(item.get("source", "") or item.get("source_path", "") or "").strip()
                if source:
                    selected_ids.append(source)
        knowledge_retrieval = state.get("knowledge_retrieval")
        if knowledge_retrieval is not None:
            for item in list(getattr(knowledge_retrieval, "evidences", []) or [])[:4]:
                source_path = str(getattr(item, "source_path", "") or "").strip()
                locator = str(getattr(item, "locator", "") or "").strip()
                if source_path or locator:
                    selected_ids.append(f"{source_path}|{locator}".strip("|"))
        return selected_ids[: len(evidence)]

    def _dropped_history_ids(self, source_history: list[dict[str, str]], selected_history: list[dict[str, str]]) -> tuple[str, ...]:
        selected = set(_history_ids(source_history, selected_history))
        dropped = [f"history:{index}" for index in range(len(source_history)) if f"history:{index}" not in selected]
        return tuple(dropped)

    def _message_tokens(self, messages: list[dict[str, str]]) -> int:
        return count_tokens("\n\n".join(f"{item['role']}: {item['content']}" for item in messages))

    def _record_assembly(self, *, state: dict[str, Any], call_site: str, assembly: ContextAssembly) -> None:
        run_id = str(state.get("run_id", "") or "").strip()
        thread_id = str(state.get("thread_id", "") or state.get("session_id", "") or "").strip()
        created_at = str(state.get("checkpoint_meta", {}).get("updated_at", "") or "").strip()
        if not run_id or not thread_id or not created_at:
            return
        try:
            context_store.record_context_assembly(
                run_id=run_id,
                thread_id=thread_id,
                call_site=call_site,
                created_at=created_at,
                assembly=assembly,
            )
        except Exception:
            return
