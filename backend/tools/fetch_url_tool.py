from __future__ import annotations

import json
from typing import Type

import html2text
import httpx
from langchain_core.callbacks.manager import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from harness.capability_types import CapabilityResult


class FetchURLInput(BaseModel):
    url: str = Field(..., description="HTTP or HTTPS URL to fetch")


class FetchURLTool(BaseTool):
    name: str = "fetch_url"
    description: str = "Fetch a URL. JSON stays JSON; HTML is converted into markdown-like plain text."
    args_schema: Type[BaseModel] = FetchURLInput

    def _format_response(self, response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            return json.dumps(response.json(), ensure_ascii=False, indent=2)[:5000]
        if "html" in content_type:
            parser = html2text.HTML2Text()
            parser.ignore_links = False
            parser.ignore_images = True
            return parser.handle(response.text)[:5000]
        return response.text[:5000]

    def _run(
        self,
        url: str,
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        return self.render_capability_result(self.execute_capability({"url": url}))

    def execute_capability(self, payload: dict[str, str]) -> CapabilityResult:
        url = str(payload.get("url", "") or "")
        try:
            with httpx.Client(follow_redirects=True, timeout=15) as client:
                response = client.get(url)
                response.raise_for_status()
        except Exception as exc:
            return CapabilityResult(
                status="failed",
                payload={},
                partial=False,
                error_type="network_error",
                error_message=f"Fetch failed: {exc}",
                retryable=True,
            )
        return CapabilityResult(
            status="success",
            payload={"text": self._format_response(response)},
            partial=False,
        )

    def render_capability_result(self, result: CapabilityResult) -> str:
        if result.payload.get("text"):
            return str(result.payload.get("text", ""))
        return result.error_message or "[no output]"

    async def _arun(
        self,
        url: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        result = await self.aexecute_capability({"url": url})
        return self.render_capability_result(result)

    async def aexecute_capability(self, payload: dict[str, str]) -> CapabilityResult:
        url = str(payload.get("url", "") or "")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                response = await client.get(url)
                response.raise_for_status()
        except Exception as exc:
            return CapabilityResult(
                status="failed",
                payload={},
                partial=False,
                error_type="network_error",
                error_message=f"Fetch failed: {exc}",
                retryable=True,
            )
        return CapabilityResult(
            status="success",
            payload={"text": self._format_response(response)},
            partial=False,
        )
