from __future__ import annotations

from typing import Any, Type

from langchain_core.callbacks.manager import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src.backend.capabilities.mcp_transport import McpTransportError
from src.backend.capabilities.types import CapabilityResult
from src.backend.capabilities.web_mcp_transport import WebDocumentMcpTransport


class WebMcpFetchInput(BaseModel):
    url: str = Field(..., description="One HTTP or HTTPS URL to fetch through the read-only Web MCP path.")


class WebMcpFetchTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = "mcp_web_fetch_url"
    description: str = (
        "Fetch one public HTTP or HTTPS URL through the read-only Web MCP adapter. "
        "Use this only when the user explicitly wants Web MCP or a document/web fetch MCP path."
    )
    args_schema: Type[BaseModel] = WebMcpFetchInput
    _transport: WebDocumentMcpTransport = PrivateAttr()

    def __init__(self, *, timeout_seconds: int, available: bool = True, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._transport = WebDocumentMcpTransport(available=available, timeout_seconds=timeout_seconds)

    def _transport_error_result(self, exc: McpTransportError) -> CapabilityResult:
        return CapabilityResult(
            status="failed",
            payload={},
            partial=False,
            error_type=exc.error_type,
            error_message=str(exc),
            retryable=bool(exc.retryable),
        )

    def execute_capability(self, payload: dict[str, Any]) -> CapabilityResult:
        try:
            response = self._transport.fetch_url(str(payload.get("url", "") or ""))
        except McpTransportError as exc:
            return self._transport_error_result(exc)
        truncated = bool(response.get("truncated", False))
        return CapabilityResult(
            status="partial" if truncated else "success",
            payload=dict(response),
            partial=truncated,
        )

    async def aexecute_capability(self, payload: dict[str, Any]) -> CapabilityResult:
        try:
            response = await self._transport.afetch_url(str(payload.get("url", "") or ""))
        except McpTransportError as exc:
            return self._transport_error_result(exc)
        truncated = bool(response.get("truncated", False))
        return CapabilityResult(
            status="partial" if truncated else "success",
            payload=dict(response),
            partial=truncated,
        )

    def render_capability_result(self, result: CapabilityResult) -> str:
        if result.payload.get("text"):
            return str(result.payload.get("text", ""))
        return result.error_message or "[no output]"

    def _run(
        self,
        url: str,
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        return self.render_capability_result(self.execute_capability({"url": url}))

    async def _arun(
        self,
        url: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        result = await self.aexecute_capability({"url": url})
        return self.render_capability_result(result)
