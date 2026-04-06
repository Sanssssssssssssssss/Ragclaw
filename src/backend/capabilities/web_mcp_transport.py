from __future__ import annotations

import json
import ssl
from urllib.parse import urlparse

import html2text
import httpx

from src.backend.capabilities.mcp_transport import McpTransportError


MAX_WEB_TEXT_CHARS = 12_000


class WebDocumentMcpTransport:
    """Phase-1 read-only web/document transport exposed through MCP-style capabilities."""

    def __init__(self, *, available: bool = True, timeout_seconds: int = 5) -> None:
        self._available = bool(available)
        self._timeout_seconds = max(1, int(timeout_seconds))

    def _verify_config_for_url(self, url: str) -> bool | ssl.SSLContext:
        parsed = urlparse(url)
        if parsed.scheme == "https":
            # Prefer the OS certificate store so desktop/dev environments with local trust roots
            # do not fail closed on public HTTPS fetches that the machine itself already trusts.
            return ssl.create_default_context()
        return True

    def _ensure_available(self) -> None:
        if not self._available:
            raise McpTransportError(
                error_type="capability_unavailable",
                message="Web MCP transport is unavailable.",
            )

    def _validate_url(self, raw_url: str) -> str:
        self._ensure_available()
        candidate = str(raw_url or "").strip()
        parsed = urlparse(candidate)
        if not candidate or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise McpTransportError(
                error_type="invalid_input",
                message="Web MCP requires one valid http or https URL.",
            )
        return candidate

    def _format_response_text(self, response: httpx.Response) -> str:
        content_type = str(response.headers.get("content-type", "") or "").lower()
        if "json" in content_type:
            try:
                return json.dumps(response.json(), ensure_ascii=False, indent=2)
            except ValueError:
                return response.text
        if "html" in content_type:
            parser = html2text.HTML2Text()
            parser.ignore_links = False
            parser.ignore_images = True
            return parser.handle(response.text)
        return response.text

    def _response_payload(self, response: httpx.Response, *, url: str) -> dict[str, object]:
        text = self._format_response_text(response)
        truncated = len(text) > MAX_WEB_TEXT_CHARS
        return {
            "url": url,
            "text": text[:MAX_WEB_TEXT_CHARS],
            "content_type": str(response.headers.get("content-type", "") or ""),
            "status_code": int(response.status_code),
            "truncated": truncated,
        }

    def fetch_url(self, url: str) -> dict[str, object]:
        validated_url = self._validate_url(url)
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=self._timeout_seconds,
                verify=self._verify_config_for_url(validated_url),
            ) as client:
                response = client.get(validated_url)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise McpTransportError(
                error_type="timeout",
                message=f"Web MCP fetch timed out for {validated_url}.",
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise McpTransportError(
                error_type="execution_error",
                message=f"Web MCP fetch failed with HTTP {exc.response.status_code}.",
            ) from exc
        except httpx.HTTPError as exc:
            raise McpTransportError(
                error_type="capability_unavailable",
                message=f"Web MCP fetch is unavailable for {validated_url}: {exc}",
            ) from exc
        return self._response_payload(response, url=validated_url)

    async def afetch_url(self, url: str) -> dict[str, object]:
        validated_url = self._validate_url(url)
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=self._timeout_seconds,
                verify=self._verify_config_for_url(validated_url),
            ) as client:
                response = await client.get(validated_url)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise McpTransportError(
                error_type="timeout",
                message=f"Web MCP fetch timed out for {validated_url}.",
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise McpTransportError(
                error_type="execution_error",
                message=f"Web MCP fetch failed with HTTP {exc.response.status_code}.",
            ) from exc
        except httpx.HTTPError as exc:
            raise McpTransportError(
                error_type="capability_unavailable",
                message=f"Web MCP fetch is unavailable for {validated_url}: {exc}",
            ) from exc
        return self._response_payload(response, url=validated_url)
