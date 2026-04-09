from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Type

from langchain_core.callbacks.manager import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src.backend.capabilities.types import CapabilityResult


class ReadFileInput(BaseModel):
    path: str = Field(..., description="Relative path inside the project root")


class ReadFileTool(BaseTool):
    name: str = "read_file"
    description: str = "Read a local file under the project root. Use relative paths like skills/foo/SKILL.md."
    args_schema: Type[BaseModel] = ReadFileInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _resolve_path(self, path: str) -> Path:
        candidate = (self._root_dir / path).resolve()
        if self._root_dir not in candidate.parents and candidate != self._root_dir:
            raise ValueError("Path traversal detected.")
        return candidate

    def _run(
        self,
        path: str,
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        return self.render_capability_result(self.execute_capability({"path": path}))

    def execute_capability(self, payload: dict[str, str]) -> CapabilityResult:
        path = str(payload.get("path", "") or "")
        try:
            file_path = self._resolve_path(path)
        except ValueError as exc:
            return CapabilityResult(
                status="failed",
                payload={},
                partial=False,
                error_type="path_traversal",
                error_message=f"Read failed: {exc}",
                retryable=False,
            )
        if not file_path.exists():
            return CapabilityResult(
                status="failed",
                payload={},
                partial=False,
                error_type="not_found",
                error_message="Read failed: file does not exist.",
                retryable=False,
            )
        if file_path.is_dir():
            return CapabilityResult(
                status="failed",
                payload={},
                partial=False,
                error_type="invalid_input",
                error_message="Read failed: path is a directory.",
                retryable=False,
            )
        return CapabilityResult(
            status="success",
            payload={"text": file_path.read_text(encoding="utf-8")[:10000]},
            partial=False,
        )

    def render_capability_result(self, result: CapabilityResult) -> str:
        if result.payload.get("text"):
            return str(result.payload.get("text", ""))
        return result.error_message or "[no output]"

    async def _arun(
        self,
        path: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        result = await self.aexecute_capability({"path": path})
        return self.render_capability_result(result)

    async def aexecute_capability(self, payload: dict[str, str]) -> CapabilityResult:
        return await asyncio.to_thread(self.execute_capability, payload)
