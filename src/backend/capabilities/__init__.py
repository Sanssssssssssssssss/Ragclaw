from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool

from src.backend.capabilities.fetch_url_tool import FetchURLTool
from src.backend.capabilities.invocation import GovernedCapabilityTool
from src.backend.capabilities.mcp_adapter import FilesystemMcpListTool, FilesystemMcpReadTool
from src.backend.capabilities.python_repl_tool import PythonReplTool
from src.backend.capabilities.read_file_tool import ReadFileTool
from src.backend.capabilities.registry import CapabilityRegistry, build_capability_registry
from src.backend.capabilities.terminal_tool import TerminalTool
from src.backend.capabilities.web_mcp_adapter import WebMcpFetchTool


def _build_raw_tools(base_dir: Path) -> list[BaseTool]:
    return [
        TerminalTool(root_dir=base_dir),
        PythonReplTool(root_dir=base_dir),
        FetchURLTool(),
        ReadFileTool(root_dir=base_dir),
        FilesystemMcpReadTool(root_dir=base_dir, timeout_seconds=5),
        FilesystemMcpListTool(root_dir=base_dir, timeout_seconds=5),
        WebMcpFetchTool(timeout_seconds=10),
    ]


def build_tools_and_registry(base_dir: Path) -> tuple[list[BaseTool], CapabilityRegistry]:
    raw_tools = _build_raw_tools(base_dir)
    registry = build_capability_registry(raw_tools)
    wrapped_tools: list[BaseTool] = [
        GovernedCapabilityTool(tool, registry.get(str(getattr(tool, "name", "") or "")))
        for tool in raw_tools
    ]
    return wrapped_tools, registry


def get_all_tools(base_dir: Path) -> list[BaseTool]:
    tools, _registry = build_tools_and_registry(base_dir)
    return tools
