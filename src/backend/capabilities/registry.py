"""Central registry for tool, skill, and future MCP-style capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.backend.capabilities.mcp_registry import (
    is_mcp_service_tool as is_filesystem_mcp_service_tool,
    mcp_spec_from_instance as filesystem_mcp_spec_from_instance,
)
from src.backend.capabilities.types import (
    DEFAULT_ERROR_SCHEMA,
    CapabilityRetryPolicy,
    CapabilitySpec,
    schema_for_model,
)
from src.backend.capabilities.web_mcp_registry import (
    is_web_mcp_service_tool,
    web_mcp_spec_from_instance,
)


GENERIC_TEXT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
    },
    "required": ["text"],
}


_TOOL_METADATA: dict[str, dict[str, Any]] = {
    "terminal": {
        "display_name": "Terminal",
        "when_to_use": "Use for workspace inspection, file listing, grep-like search, or controlled local commands.",
        "when_not_to_use": "Do not use for known single-file reads, live web lookups, or purely reasoning-only answers.",
        "risk_level": "high",
        "timeout_seconds": 30,
        "retry_policy": CapabilityRetryPolicy(max_retries=0),
        "approval_required": False,
        "tags": ("workspace", "shell", "inspection"),
        "budget_cost": 2,
        "repeated_call_limit": 4,
    },
    "python_repl": {
        "display_name": "Python REPL",
        "when_to_use": "Use for structured parsing, calculations, dataframe work, or short file-backed transforms.",
        "when_not_to_use": "Do not use for shell inspection, direct file reads, or open-ended autonomous loops.",
        "risk_level": "medium",
        "timeout_seconds": 15,
        "retry_policy": CapabilityRetryPolicy(max_retries=0),
        "approval_required": False,
        "tags": ("workspace", "python", "analysis"),
        "budget_cost": 2,
        "repeated_call_limit": 3,
    },
    "read_file": {
        "display_name": "Read File",
        "when_to_use": "Use when the exact local file path is already known and you need its contents.",
        "when_not_to_use": "Do not use for directory search, repeated broad exploration, or live web content.",
        "risk_level": "low",
        "timeout_seconds": 10,
        "retry_policy": CapabilityRetryPolicy(max_retries=0),
        "approval_required": False,
        "tags": ("workspace", "file", "read_only"),
        "budget_cost": 1,
        "repeated_call_limit": 6,
    },
    "fetch_url": {
        "display_name": "Fetch URL",
        "when_to_use": "Use for official docs, latest online facts, public links, or weather-like HTTP fetches.",
        "when_not_to_use": "Do not use for workspace files, indexed knowledge-base QA, or requests that do not need the web.",
        "risk_level": "medium",
        "timeout_seconds": 15,
        "retry_policy": CapabilityRetryPolicy(
            max_retries=1,
            backoff_seconds=1.0,
            retryable_error_types=("network_error", "timeout"),
        ),
        "approval_required": False,
        "tags": ("web", "http", "live_data"),
        "budget_cost": 1,
        "repeated_call_limit": 4,
    },
}


_SKILL_SPECS: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        capability_id="skill.get_weather",
        capability_type="skill",
        display_name="Weather Skill",
        description="Skill guidance for explicit weather lookup by city or forecast window.",
        when_to_use="Use when the user explicitly asks for weather, forecast, rain, wind, or temperature by location.",
        when_not_to_use="Do not use for general web research, workspace files, or indexed knowledge-base questions.",
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "allowed_capabilities": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["message"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "guidance": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["guidance"],
        },
        error_schema=DEFAULT_ERROR_SCHEMA,
        risk_level="medium",
        timeout_seconds=10,
        retry_policy=CapabilityRetryPolicy(max_retries=0),
        approval_required=False,
        tags=("skill", "weather", "web"),
        budget_cost=1,
        repeated_call_limit=1,
        required_capabilities=("fetch_url",),
    ),
    CapabilitySpec(
        capability_id="skill.kb_retriever",
        capability_type="skill",
        display_name="Legacy KB Retriever Skill",
        description="Legacy local knowledge-directory search workflow kept only for governance visibility.",
        when_to_use="Use only for explicitly legacy local knowledge-directory search workflows.",
        when_not_to_use="Do not use for the formal indexed knowledge QA path or normal report/document retrieval.",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        output_schema={"type": "object", "properties": {"guidance": {"type": "array", "items": {"type": "string"}}}},
        error_schema=DEFAULT_ERROR_SCHEMA,
        risk_level="high",
        timeout_seconds=10,
        retry_policy=CapabilityRetryPolicy(max_retries=0),
        approval_required=False,
        tags=("skill", "legacy", "disabled"),
        budget_cost=2,
        repeated_call_limit=1,
        enabled=False,
        required_capabilities=("read_file", "terminal", "python_repl"),
    ),
    CapabilitySpec(
        capability_id="skill.retry_lesson_capture",
        capability_type="skill",
        display_name="Retry Lesson Capture Skill",
        description="Internal post-recovery lesson-capture workflow reserved for future recovery support.",
        when_to_use="Use only for internal post-recovery lesson capture after a failure then success.",
        when_not_to_use="Do not use for normal user-facing execution.",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        output_schema={"type": "object", "properties": {"guidance": {"type": "array", "items": {"type": "string"}}}},
        error_schema=DEFAULT_ERROR_SCHEMA,
        risk_level="high",
        timeout_seconds=10,
        retry_policy=CapabilityRetryPolicy(max_retries=0),
        approval_required=False,
        tags=("skill", "recovery", "disabled"),
        budget_cost=2,
        repeated_call_limit=1,
        enabled=False,
        required_capabilities=("read_file", "python_repl"),
    ),
    CapabilitySpec(
        capability_id="skill.web_search",
        capability_type="skill",
        display_name="Web Search Skill",
        description="Skill guidance for latest/current online facts, official docs, links, pricing, or news.",
        when_to_use="Use for explicit latest/current online facts, official docs, links, homepage, pricing, or news.",
        when_not_to_use="Do not use for knowledge-base QA, workspace operations, or requests that do not need the web.",
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "allowed_capabilities": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["message"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "guidance": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["guidance"],
        },
        error_schema=DEFAULT_ERROR_SCHEMA,
        risk_level="medium",
        timeout_seconds=10,
        retry_policy=CapabilityRetryPolicy(max_retries=0),
        approval_required=False,
        tags=("skill", "web", "research"),
        budget_cost=1,
        repeated_call_limit=1,
        required_capabilities=("fetch_url",),
    ),
)


@dataclass
class CapabilityRegistry:
    _specs: dict[str, CapabilitySpec]

    def get(self, capability_id: str) -> CapabilitySpec:
        spec = self._specs.get(capability_id)
        if spec is None:
            raise KeyError(f"unknown capability_id={capability_id}")
        return spec

    def list(self, *, capability_type: str | None = None, enabled_only: bool = False) -> list[CapabilitySpec]:
        items = list(self._specs.values())
        if capability_type is not None:
            items = [item for item in items if item.capability_type == capability_type]
        if enabled_only:
            items = [item for item in items if item.enabled]
        return sorted(items, key=lambda item: item.capability_id)

    def to_dict(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.list()]


def _tool_spec_from_instance(tool: Any) -> CapabilitySpec:
    name = str(getattr(tool, "name", "") or "").strip()
    if is_filesystem_mcp_service_tool(name):
        return filesystem_mcp_spec_from_instance(tool)
    if is_web_mcp_service_tool(name):
        return web_mcp_spec_from_instance(tool)
    if name not in _TOOL_METADATA:
        raise KeyError(f"missing tool capability metadata for {name}")
    metadata = dict(_TOOL_METADATA[name])
    return CapabilitySpec(
        capability_id=name,
        capability_type="tool",
        display_name=str(metadata["display_name"]),
        description=str(getattr(tool, "description", "") or metadata["display_name"]),
        when_to_use=str(metadata["when_to_use"]),
        when_not_to_use=str(metadata["when_not_to_use"]),
        input_schema=schema_for_model(getattr(tool, "args_schema", None)),
        output_schema=dict(GENERIC_TEXT_OUTPUT_SCHEMA),
        error_schema=dict(DEFAULT_ERROR_SCHEMA),
        risk_level=metadata["risk_level"],
        timeout_seconds=int(metadata["timeout_seconds"]),
        retry_policy=metadata["retry_policy"],
        approval_required=bool(metadata["approval_required"]),
        tags=tuple(str(item) for item in metadata["tags"]),
        budget_cost=int(metadata["budget_cost"]),
        repeated_call_limit=int(metadata["repeated_call_limit"]),
    )


def build_capability_registry(tools: Iterable[Any]) -> CapabilityRegistry:
    specs = {_spec.capability_id: _spec for _spec in _SKILL_SPECS}
    for tool in tools:
        spec = _tool_spec_from_instance(tool)
        specs[spec.capability_id] = spec
    return CapabilityRegistry(specs)


def skill_capability_specs() -> list[CapabilitySpec]:
    return list(_SKILL_SPECS)
