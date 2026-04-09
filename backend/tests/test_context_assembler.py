from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.context.assembler import ContextAssembler


class _Evidence:
    def __init__(self, source_path: str, locator: str, snippet: str) -> None:
        self.source_path = source_path
        self.locator = locator
        self.snippet = snippet


class _KnowledgeResult:
    def __init__(self) -> None:
        self.evidences = [
            _Evidence("knowledge/report.pdf", "page 3", "Gross margin reached 12.4%."),
            _Evidence("knowledge/report.pdf", "page 4", "Operating margin reached 8.1%."),
        ]


class ContextAssemblerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assembler = ContextAssembler()
        self.base_state = {
            "user_message": "Use the MCP result but keep the answer grounded.",
            "history": [{"role": "user", "content": f"older message {index}"} for index in range(8)],
            "working_memory": {
                "current_goal": "answer grounded",
                "latest_user_intent": "capability",
                "active_constraints": ["stay grounded", "do not use trace"],
                "active_entities": ["Ragclaw"],
                "active_artifacts": ["knowledge/report.pdf"],
                "latest_capability_results": ["mcp_web_fetch_url [success] :: fetched snippet"],
                "latest_retrieval_summary": "memory_hits=1",
                "unresolved_items": ["approval pending for python_repl"],
            },
            "episodic_summary": {
                "key_facts": ["User prefers grounded answers."],
                "completed_subtasks": ["mcp_web_fetch_url::success"],
                "rejected_paths": ["python_repl rejected"],
                "important_decisions": ["route=capability source=rules"],
                "important_artifacts": ["knowledge/report.pdf"],
                "open_loops": ["pending approval for python_repl"],
            },
            "capability_results": [
                {"capability_id": "mcp_web_fetch_url", "status": "success", "payload": {"text": "Page text snippet"}}
            ],
            "memory_retrieval": [{"source": "memory/MEMORY.md", "text": "Remember to stay grounded"}],
            "knowledge_retrieval": _KnowledgeResult(),
            "checkpoint_meta": {"run_status": "fresh"},
        }

    def test_capability_context_assembly_includes_budgeted_sections(self) -> None:
        assembly = self.assembler.assemble(path_kind="capability_path", state=self.base_state)
        self.assertEqual(assembly.path_kind, "capability_path")
        self.assertTrue(assembly.history_messages)
        self.assertIn("[Working memory]", assembly.working_memory_block)
        self.assertIn("[Episodic summary]", assembly.episodic_block)
        self.assertIn("[Capability outputs]", assembly.artifacts_block)
        self.assertIn("[Retrieval evidence]", assembly.retrieval_block)
        self.assertIn("[Context policy]", assembly.envelope.system_block)
        self.assertIn("raw trace events", assembly.excluded_from_prompt)

    def test_resumed_hitl_path_prefers_resume_context(self) -> None:
        resumed_state = {
            **self.base_state,
            "interrupt_request": {"capability_id": "python_repl"},
            "checkpoint_meta": {"run_status": "restoring"},
        }
        assembly = self.assembler.assemble(path_kind="direct_answer", state=resumed_state)
        self.assertEqual(assembly.path_kind, "resumed_hitl")
        self.assertLessEqual(len(assembly.history_messages), len(self.base_state["history"]))
        self.assertIn("approval pending", assembly.working_memory_block)


if __name__ == "__main__":
    unittest.main()
