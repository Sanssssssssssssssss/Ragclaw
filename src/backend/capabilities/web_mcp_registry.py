from __future__ import annotations

from typing import Any

from src.backend.capabilities.types import (
    DEFAULT_ERROR_SCHEMA,
    CapabilityRetryPolicy,
    CapabilitySpec,
    schema_for_model,
)


WEB_MCP_METADATA: dict[str, dict[str, Any]] = {
    "mcp_web_fetch_url": {
        "display_name": "Web MCP Fetch URL",
        "description": "Fetch one public web/document URL through the phase-1 read-only Web MCP capability.",
        "when_to_use": (
            "Use when the user explicitly asks for Web MCP or document fetch MCP and one public URL is already known."
        ),
        "when_not_to_use": (
            "Do not use for workspace files, multi-step browsing, login-gated pages, or requests that do not need a live URL fetch."
        ),
        "output_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "text": {"type": "string"},
                "content_type": {"type": "string"},
                "status_code": {"type": "integer"},
                "truncated": {"type": "boolean"},
            },
            "required": ["url", "text", "content_type", "status_code", "truncated"],
        },
        "risk_level": "medium",
        "timeout_seconds": 3,
        "retry_policy": CapabilityRetryPolicy(max_retries=0),
        "approval_required": False,
        "tags": ("mcp", "web", "document", "read_only", "phase1"),
        "budget_cost": 1,
        "repeated_call_limit": 2,
    }
}


def is_web_mcp_service_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip() in WEB_MCP_METADATA


def web_mcp_spec_from_instance(tool: Any) -> CapabilitySpec:
    name = str(getattr(tool, "name", "") or "").strip()
    metadata = WEB_MCP_METADATA.get(name)
    if metadata is None:
        raise KeyError(f"missing web MCP capability metadata for {name}")
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
