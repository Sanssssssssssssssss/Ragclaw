from __future__ import annotations

import re
from typing import Iterable

from src.backend.context.models import ContextPathKind, MemoryManifest


_WORD_PATTERN = re.compile(r"[a-z0-9_./:-]+", re.IGNORECASE)


def tokenize(text: str) -> tuple[str, ...]:
    tokens = [match.group(0).lower() for match in _WORD_PATTERN.finditer(str(text or ""))]
    return tuple(dict.fromkeys(token for token in tokens if len(token) > 1))


def score_manifest(
    manifest: MemoryManifest,
    *,
    query: str,
    path_kind: ContextPathKind = "direct_answer",
    recent_terms: Iterable[str] = (),
) -> float:
    query_terms = set(tokenize(query))
    context_terms = set(token.lower() for token in recent_terms if str(token).strip())
    manifest_terms = set(tokenize(" ".join([manifest.title, manifest.summary, " ".join(manifest.tags)])))
    overlap = len((query_terms | context_terms) & manifest_terms)
    score = float(overlap)
    score += manifest.confidence * 4.0
    score += manifest.promotion_priority / 25.0
    if manifest.direct_prompt:
        score += 1.0
    if manifest.status == "active":
        score += 0.5
    if manifest.freshness == "fresh":
        score += 0.75
    elif manifest.freshness == "aging":
        score += 0.15
    else:
        score -= 1.25
    if manifest.conflict_flag:
        score -= 0.8
    if manifest.status in {"superseded", "invalidated", "dropped"}:
        score -= 5.0
    prompt_paths = set(str(item) for item in manifest.applicability.get("prompt_paths", []) or [])
    if prompt_paths and path_kind in prompt_paths:
        score += 1.1
    if prompt_paths and path_kind not in prompt_paths:
        score -= 1.6
    if not manifest.direct_prompt and path_kind in {"direct_answer", "knowledge_qa"}:
        score -= 0.35
    if manifest.memory_type == "artifact_map" and path_kind == "direct_answer":
        score -= 1.25
    if manifest.memory_type == "workflow_rule" and path_kind in {"capability_path", "recovery_path", "resumed_hitl"}:
        score += 0.75
    if manifest.memory_type == "project_fact" and path_kind == "knowledge_qa":
        score += 0.55
    if manifest.memory_type == "preference_feedback" and path_kind in {"direct_answer", "capability_path"}:
        score += 0.55
    return score


def render_memory_index(manifests: list[MemoryManifest]) -> str:
    lines = ["# MEMORY.md", "", "Generated memory manifest index for governed context recall.", ""]
    if not manifests:
        lines.append("- No active governed memories yet.")
        return "\n".join(lines).strip() + "\n"

    for manifest in manifests:
        status_bits: list[str] = [manifest.memory_type, manifest.scope]
        if manifest.status != "active":
            status_bits.append(manifest.status)
        if manifest.conflict_flag:
            status_bits.append("conflict")
        if manifest.freshness != "fresh":
            status_bits.append(manifest.freshness)
        suffix = f" [{' | '.join(status_bits)}]" if status_bits else ""
        lines.append(f"- {manifest.title}: {manifest.summary}{suffix}")
    return "\n".join(lines).strip() + "\n"
