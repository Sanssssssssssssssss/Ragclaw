from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Type

from langchain_core.callbacks.manager import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src.backend.capabilities.mcp_transport import FilesystemMcpTransport, McpTransportError
from src.backend.capabilities.types import CapabilityResult


class FilesystemReadInput(BaseModel):
    path: str = Field(..., description="Relative file path inside the configured Filesystem MCP root.")


class FilesystemListInput(BaseModel):
    path: str = Field(".", description="Relative directory path inside the configured Filesystem MCP root.")


class _FilesystemMcpTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _transport: FilesystemMcpTransport = PrivateAttr()
    _timeout_seconds: int = PrivateAttr()

    def __init__(self, root_dir: Path, *, timeout_seconds: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._transport = FilesystemMcpTransport(root_dir)
        self._timeout_seconds = int(timeout_seconds)

    def _run_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def _transport_error_result(self, exc: McpTransportError) -> CapabilityResult:
        return CapabilityResult(
            status="failed",
            payload={},
            partial=False,
            error_type=exc.error_type,
            error_message=str(exc),
            retryable=bool(exc.retryable),
        )

    def _timeout_result(self) -> CapabilityResult:
        return CapabilityResult(
            status="failed",
            payload={},
            partial=False,
            error_type="timeout",
            error_message=f"Filesystem MCP timed out after {self._timeout_seconds} seconds.",
            retryable=False,
        )

    def execute_capability(self, payload: dict[str, Any]) -> CapabilityResult:
        try:
            response = self._run_operation(payload)
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
            return await asyncio.wait_for(
                asyncio.to_thread(self.execute_capability, payload),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            return self._timeout_result()

    def render_capability_result(self, result: CapabilityResult) -> str:
        payload = dict(result.payload)
        if payload.get("text"):
            return str(payload.get("text", ""))
        if payload.get("entries"):
            entries = [str(item) for item in payload.get("entries", [])]
            return "\n".join(entries) or "[empty directory]"
        return result.error_message or "[no output]"


class FilesystemMcpReadTool(_FilesystemMcpTool):
    name: str = "mcp_filesystem_read_file"
    description: str = (
        "Read one local file through the read-only Filesystem MCP adapter. "
        "Use this only when the user explicitly wants Filesystem MCP or the exact relative path is already known."
    )
    args_schema: Type[BaseModel] = FilesystemReadInput

    def _run(
        self,
        path: str,
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        return self.render_capability_result(self.execute_capability({"path": path}))

    async def _arun(
        self,
        path: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        result = await self.aexecute_capability({"path": path})
        return self.render_capability_result(result)

    def _run_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._transport.read_file(str(payload.get("path", "") or ""))


class FilesystemMcpListTool(_FilesystemMcpTool):
    name: str = "mcp_filesystem_list_directory"
    description: str = (
        "List one local directory through the read-only Filesystem MCP adapter. "
        "Use this only when the user explicitly wants Filesystem MCP or a read-only directory listing."
    )
    args_schema: Type[BaseModel] = FilesystemListInput

    def _run(
        self,
        path: str = ".",
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        return self.render_capability_result(self.execute_capability({"path": path}))

    async def _arun(
        self,
        path: str = ".",
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        result = await self.aexecute_capability({"path": path})
        return self.render_capability_result(result)

    def _run_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._transport.list_directory(str(payload.get("path", ".") or "."))
