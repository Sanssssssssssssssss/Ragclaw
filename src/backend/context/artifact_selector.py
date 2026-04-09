from __future__ import annotations

from typing import Any

from src.backend.context.models import ContextPathKind


def _safe_text(value: Any, *, limit: int = 600) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " ..."


class ArtifactSelector:
    def select_capability_outputs(self, state: dict[str, Any], *, path_kind: ContextPathKind) -> list[str]:
        results = list(state.get("capability_results", []) or [])
        if not results or path_kind == "direct_answer":
            return []
        selected: list[str] = []
        for item in results[-3:]:
            if not isinstance(item, dict):
                continue
            capability_id = str(item.get("capability_id", "") or "capability")
            status = str(item.get("status", "") or "")
            error_type = str(item.get("error_type", "") or "")
            payload = item.get("payload", {})
            payload_text = _safe_text(payload if isinstance(payload, str) else payload)
            line = f"{capability_id} [{status}]"
            if error_type:
                line += f" error={error_type}"
            if payload_text:
                line += f"\n{payload_text}"
            selected.append(line.strip())
        return selected

    def select_retrieval_evidence(self, state: dict[str, Any], *, path_kind: ContextPathKind) -> list[str]:
        items: list[str] = []
        memory_retrieval = list(state.get("memory_retrieval", []) or [])
        if memory_retrieval and path_kind in {"direct_answer", "capability_path", "resumed_hitl", "recovery_path"}:
            for item in memory_retrieval[:2]:
                if not isinstance(item, dict):
                    continue
                source = str(item.get("source", "") or item.get("source_path", "") or "memory")
                snippet = _safe_text(item.get("text", "") or item.get("snippet", ""))
                items.append(f"{source}\n{snippet}".strip())

        knowledge_retrieval = state.get("knowledge_retrieval")
        if knowledge_retrieval is not None and path_kind in {"knowledge_qa", "resumed_hitl", "recovery_path"}:
            for evidence in list(getattr(knowledge_retrieval, "evidences", []) or [])[:4]:
                source_path = str(getattr(evidence, "source_path", "") or "").strip()
                locator = str(getattr(evidence, "locator", "") or "").strip()
                snippet = _safe_text(getattr(evidence, "snippet", "") or "")
                header = source_path
                if locator:
                    header = f"{header} | {locator}" if header else locator
                items.append(f"{header}\n{snippet}".strip())
        return items
