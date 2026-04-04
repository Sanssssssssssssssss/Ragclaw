from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool

from harness.capability_invocation import GovernedCapabilityTool
from harness.capability_registry import CapabilityRegistry, build_capability_registry
from tools.fetch_url_tool import FetchURLTool
from tools.python_repl_tool import PythonReplTool
from tools.read_file_tool import ReadFileTool
from tools.terminal_tool import TerminalTool


def _build_raw_tools(base_dir: Path) -> list[BaseTool]:
    return [
        TerminalTool(root_dir=base_dir),
        PythonReplTool(root_dir=base_dir),
        FetchURLTool(),
        ReadFileTool(root_dir=base_dir),
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
