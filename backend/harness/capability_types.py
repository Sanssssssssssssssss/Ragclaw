"""Shared capability metadata, invocation, and result contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel


CapabilityType = Literal["tool", "skill", "mcp_service", "function"]
CapabilityRiskLevel = Literal["low", "medium", "high"]
CapabilityStatus = Literal["success", "partial", "failed", "blocked"]


def schema_for_model(model: type[BaseModel] | None) -> dict[str, Any]:
    """Return a JSON-schema-like dict for one optional Pydantic model."""

    if model is None:
        return {"type": "object", "properties": {}, "additionalProperties": True}
    return model.model_json_schema()


@dataclass(frozen=True)
class CapabilityRetryPolicy:
    max_retries: int = 0
    backoff_seconds: float = 0.0
    retryable_error_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["retryable_error_types"] = list(self.retryable_error_types)
        return payload


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str
    capability_type: CapabilityType
    display_name: str
    description: str
    when_to_use: str
    when_not_to_use: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    error_schema: dict[str, Any]
    risk_level: CapabilityRiskLevel
    timeout_seconds: int
    retry_policy: CapabilityRetryPolicy
    approval_required: bool
    tags: tuple[str, ...] = ()
    budget_cost: int = 1
    repeated_call_limit: int = 3
    enabled: bool = True
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.budget_cost <= 0:
            raise ValueError("budget_cost must be positive")
        if self.repeated_call_limit <= 0:
            raise ValueError("repeated_call_limit must be positive")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        payload["required_capabilities"] = list(self.required_capabilities)
        payload["retry_policy"] = self.retry_policy.to_dict()
        return payload


@dataclass(frozen=True)
class CapabilityInvocation:
    call_id: str
    run_id: str
    session_id: str | None
    capability_id: str
    capability_type: CapabilityType
    payload: dict[str, Any]
    requested_at: str
    source: str = "runtime"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityResult:
    status: CapabilityStatus
    payload: dict[str, Any] = field(default_factory=dict)
    partial: bool = False
    error_type: str = ""
    error_message: str = ""
    retryable: bool = False
    latency_ms: int = 0
    call_id: str = ""
    retry_count: int = 0

    def __post_init__(self) -> None:
        if self.status == "success" and (self.error_type or self.error_message):
            raise ValueError("success result must not include error fields")
        if self.status == "partial" and not self.partial:
            raise ValueError("partial result must set partial=True")
        if self.status in {"failed", "blocked"} and not self.error_type:
            raise ValueError("failed or blocked result requires error_type")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "error_type": {"type": "string"},
        "error_message": {"type": "string"},
        "retryable": {"type": "boolean"},
    },
    "required": ["status", "error_type", "error_message", "retryable"],
}
