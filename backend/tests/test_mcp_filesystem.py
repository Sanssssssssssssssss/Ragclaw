from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.backend.capabilities.governance import CapabilityBudgetPolicy, CapabilityGovernor
from src.backend.capabilities.invocation import CapabilityRuntimeContext, capability_runtime_scope, invoke_capability
from src.backend.capabilities.mcp_adapter import FilesystemMcpListTool, FilesystemMcpReadTool
from src.backend.capabilities.registry import CapabilityRegistry


@dataclass(frozen=True)
class _Handle:
    run_id: str = "run-mcp"
    metadata: object = None

    def __post_init__(self):
        if self.metadata is None:
            object.__setattr__(self, "metadata", type("_Meta", (), {"session_id": "session-mcp"})())


class _Runtime:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def now(self) -> str:
        return "2026-04-06T12:00:00Z"

    async def emit(self, handle, name: str, payload: dict) -> None:
        self.events.append((name, dict(payload)))

    def record_internal_event(self, run_id: str, name: str, payload: dict) -> None:
        self.events.append((name, dict(payload)))


class FilesystemMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_and_list_are_read_only_and_structured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "docs").mkdir()
            (root / "docs" / "note.txt").write_text("hello mcp", encoding="utf-8")
            (root / "docs" / "alpha.txt").write_text("a", encoding="utf-8")

            read_tool = FilesystemMcpReadTool(root_dir=root, timeout_seconds=5)
            list_tool = FilesystemMcpListTool(root_dir=root, timeout_seconds=5)

            read_result = read_tool.execute_capability({"path": "docs/note.txt"})
            list_result = list_tool.execute_capability({"path": "docs"})
            traversal_result = read_tool.execute_capability({"path": "../outside.txt"})

            self.assertEqual(read_result.status, "success")
            self.assertEqual(read_result.payload["text"], "hello mcp")
            self.assertEqual(list_result.status, "success")
            self.assertIn("note.txt", list_result.payload["entries"])
            self.assertEqual(traversal_result.status, "failed")
            self.assertEqual(traversal_result.error_type, "path_traversal")

    async def test_repeated_call_limit_blocks_third_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "docs").mkdir()
            (root / "docs" / "note.txt").write_text("hello mcp", encoding="utf-8")

            tool = FilesystemMcpReadTool(root_dir=root, timeout_seconds=5)
            from src.backend.capabilities.mcp_registry import mcp_spec_from_instance

            spec = mcp_spec_from_instance(tool)
            registry = CapabilityRegistry({spec.capability_id: spec})
            runtime = _Runtime()
            context = CapabilityRuntimeContext(
                runtime=runtime,
                handle=_Handle(),
                registry=registry,
                governor=CapabilityGovernor(CapabilityBudgetPolicy(max_budget_cost=10, max_total_calls=10)),
            )

            async with capability_runtime_scope(context):
                first = await invoke_capability(
                    spec=spec,
                    payload={"path": "docs/note.txt"},
                    execute_async=tool.aexecute_capability,
                )
                second = await invoke_capability(
                    spec=spec,
                    payload={"path": "docs/note.txt"},
                    execute_async=tool.aexecute_capability,
                )
                third = await invoke_capability(
                    spec=spec,
                    payload={"path": "docs/note.txt"},
                    execute_async=tool.aexecute_capability,
                )

            self.assertEqual(first.status, "success")
            self.assertEqual(second.status, "success")
            self.assertEqual(third.status, "blocked")
            self.assertEqual(third.error_type, "repeated_call_limit")
            self.assertIn("capability.completed", [name for name, _payload in runtime.events])
            self.assertIn("capability.blocked", [name for name, _payload in runtime.events])


if __name__ == "__main__":
    unittest.main()
