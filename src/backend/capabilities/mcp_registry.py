from __future__ import annotations

from typing import Any

from src.backend.capabilities.types import (
    DEFAULT_ERROR_SCHEMA,
    CapabilityRetryPolicy,
    CapabilitySpec,
    schema_for_model,
)


FILESYSTEM_MCP_METADATA: dict[str, dict[str, Any]] = {
    "mcp_filesystem_read_file": {
        "display_name": "Filesystem MCP Read File",
        "description": "Read one local file through the phase-1 read-only Filesystem MCP capability.",
        "when_to_use": (
            "Use when the user explicitly asks to use Filesystem MCP or when a read-only exact-path file read is the best MCP fit."
        ),
        "when_not_to_use": (
            "Do not use for broad workspace search, file writes, destructive actions, or requests that can be answered without filesystem access."
        ),
        "output_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "text": {"type": "string"},
                "truncated": {"type": "boolean"},
            },
            "required": ["path", "text", "truncated"],
        },
        "risk_level": "low",
        "timeout_seconds": 5,
        "retry_policy": CapabilityRetryPolicy(max_retries=0),
        "approval_required": False,
        "tags": ("mcp", "filesystem", "read_only", "phase1"),
        "budget_cost": 1,
        "repeated_call_limit": 2,
    },
    "mcp_filesystem_list_directory": {
        "display_name": "Filesystem MCP List Directory",
        "description": "List one local directory through the phase-1 read-only Filesystem MCP capability.",
        "when_to_use": (
            "Use when the user explicitly asks to use Filesystem MCP or needs a read-only directory listing for a known path."
        ),
        "when_not_to_use": (
            "Do not use for file writes, shell execution, destructive actions, or open-ended workspace exploration."
        ),
        "output_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "entries": {"type": "array", "items": {"type": "string"}},
                "text": {"type": "string"},
                "truncated": {"type": "boolean"},
            },
            "required": ["path", "entries", "text", "truncated"],
        },
        "risk_level": "low",
        "timeout_seconds": 5,
        "retry_policy": CapabilityRetryPolicy(max_retries=0),
        "approval_required": False,
        "tags": ("mcp", "filesystem", "directory", "read_only", "phase1"),
        "budget_cost": 1,
        "repeated_call_limit": 2,
    },
}


def is_mcp_service_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip() in FILESYSTEM_MCP_METADATA


def mcp_spec_from_instance(tool: Any) -> CapabilitySpec:
    name = str(getattr(tool, "name", "") or "").strip()
    metadata = FILESYSTEM_MCP_METADATA.get(name)
    if metadata is None:
        raise KeyError(f"missing MCP capability metadata for {name}")
    return CapabilitySpec(
        capability_id=name,
        capability_type="mcp_service",
        display_name=str(metadata["display_name"]),
        description=str(metadata["description"]),
        when_to_use=str(metadata["when_to_use"]),
        when_not_to_use=str(metadata["when_not_to_use"]),
        input_schema=schema_for_model(getattr(tool, "args_schema", None)),
        output_schema=dict(metadata["output_schema"]),
        error_schema=dict(DEFAULT_ERROR_SCHEMA),
        risk_level=metadata["risk_level"],
        timeout_seconds=int(metadata["timeout_seconds"]),
        retry_policy=metadata["retry_policy"],
        approval_required=bool(metadata["approval_required"]),
        tags=tuple(str(item) for item in metadata["tags"]),
        budget_cost=int(metadata["budget_cost"]),
        repeated_call_limit=int(metadata["repeated_call_limit"]),
    )
