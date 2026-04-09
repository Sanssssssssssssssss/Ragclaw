from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_READ_CHARS = 10_000
MAX_DIRECTORY_ENTRIES = 200


@dataclass(frozen=True)
class McpTransportError(Exception):
    error_type: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


class FilesystemMcpTransport:
    """Phase-1 read-only filesystem transport exposed through MCP-style capabilities."""

    def __init__(self, root_dir: Path, *, available: bool = True) -> None:
        self._root_dir = root_dir.resolve()
        self._available = bool(available)

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    def _ensure_available(self) -> None:
        if not self._available:
            raise McpTransportError(
                error_type="capability_unavailable",
                message="Filesystem MCP transport is unavailable.",
            )

    def _resolve_path(self, raw_path: str) -> Path:
        self._ensure_available()
        candidate_text = str(raw_path or "").strip()
        if not candidate_text:
            raise McpTransportError(
                error_type="invalid_input",
                message="Filesystem MCP requires a non-empty relative path.",
            )

        candidate = (self._root_dir / candidate_text).resolve()
        if candidate != self._root_dir and self._root_dir not in candidate.parents:
            raise McpTransportError(
                error_type="path_traversal",
                message="Filesystem MCP blocked a path traversal attempt.",
            )
        return candidate

    def read_file(self, path: str) -> dict[str, Any]:
        file_path = self._resolve_path(path)
        if not file_path.exists():
            raise McpTransportError(
                error_type="not_found",
                message="Filesystem MCP read failed: file does not exist.",
            )
        if file_path.is_dir():
            raise McpTransportError(
                error_type="invalid_input",
                message="Filesystem MCP read failed: path is a directory.",
            )

        text = file_path.read_text(encoding="utf-8")
        truncated = len(text) > MAX_READ_CHARS
        return {
            "path": str(file_path.relative_to(self._root_dir)).replace("\\", "/"),
            "text": text[:MAX_READ_CHARS],
            "truncated": truncated,
        }

    def list_directory(self, path: str) -> dict[str, Any]:
        directory_path = self._resolve_path(path)
        if not directory_path.exists():
            raise McpTransportError(
                error_type="not_found",
                message="Filesystem MCP list failed: directory does not exist.",
            )
        if not directory_path.is_dir():
            raise McpTransportError(
                error_type="invalid_input",
                message="Filesystem MCP list failed: path is not a directory.",
            )

        entries = sorted(item.name for item in directory_path.iterdir())
        truncated = len(entries) > MAX_DIRECTORY_ENTRIES
        visible_entries = entries[:MAX_DIRECTORY_ENTRIES]
        return {
            "path": str(directory_path.relative_to(self._root_dir)).replace("\\", "/") or ".",
            "entries": visible_entries,
            "text": "\n".join(visible_entries) or "[empty directory]",
            "truncated": truncated,
        }
