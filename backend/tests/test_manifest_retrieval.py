from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.context.assembler import ContextAssembler
from src.backend.context.store import context_store


class ManifestRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def test_context_assembler_uses_manifest_first_and_hydrates_only_prompt_safe_hits(self) -> None:
        context_store.insert_memory(
            kind="semantic",
            namespace="project:backend",
            memory_type="project_fact",
            scope="project",
            title="Release freeze fact",
            content="The release freeze starts on 2026-05-01.",
            summary="Release freeze starts on 2026-05-01.",
            tags=("release", "freeze"),
            source="unit_test",
            created_at="2026-04-09T10:00:00Z",
            fingerprint="semantic-freeze",
            confidence=0.9,
            direct_prompt=True,
            promotion_priority=90,
            applicability={"prompt_paths": ["capability_path", "knowledge_qa"]},
            conflict_key="project-freeze",
        )
        recently_used = context_store.insert_memory(
            kind="procedural",
            namespace="project:backend",
            memory_type="workflow_rule",
            scope="project",
            title="Recently used grounded workflow",
            content="Always answer with grounded evidence and avoid raw audit data.",
            summary="Grounded answers only.",
            tags=("grounded", "workflow"),
            source="unit_test",
            created_at="2026-04-09T10:01:00Z",
            fingerprint="procedural-grounded-used",
            confidence=0.91,
            direct_prompt=True,
            promotion_priority=95,
            applicability={"prompt_paths": ["capability_path"]},
            conflict_key="workflow-grounded-used",
        )
        context_store.insert_memory(
            kind="procedural",
            namespace="project:backend",
            memory_type="workflow_rule",
            scope="project",
            title="Fallback grounded workflow",
            content="Prefer grounded evidence and avoid unsupported audit details.",
            summary="Fallback grounded workflow.",
            tags=("grounded", "workflow"),
            source="unit_test",
            created_at="2026-04-09T10:01:30Z",
            fingerprint="procedural-grounded-fallback",
            confidence=0.9,
            direct_prompt=True,
            promotion_priority=92,
            applicability={"prompt_paths": ["capability_path"]},
            conflict_key="workflow-grounded-fallback",
        )
        context_store.insert_memory(
            kind="semantic",
            namespace="project:backend",
            memory_type="artifact_map",
            scope="project",
            title="Artifact map only",
            content="knowledge/report.pdf maps to the release report.",
            summary="Artifact map for report.",
            tags=("artifact", "report"),
            source="unit_test",
            created_at="2026-04-09T10:02:00Z",
            fingerprint="artifact-map-report",
            confidence=0.95,
            direct_prompt=False,
            promotion_priority=60,
            applicability={"prompt_paths": ["capability_path"]},
            conflict_key="artifact-map",
        )

        assembler = ContextAssembler(base_dir=self.base_dir)
        assembly = assembler.assemble(
            path_kind="capability_path",
            state={
                "run_id": "run-1",
                "session_id": "session-1",
                "thread_id": "session-1",
                "user_message": "Use the release freeze fact and stay grounded.",
                "history": [{"role": "user", "content": "previous question"}],
                "working_memory": {
                    "current_goal": "answer release question",
                    "latest_user_intent": "capability",
                    "active_constraints": ["stay grounded"],
                    "active_entities": ["Ragclaw"],
                    "active_artifacts": ["knowledge/report.pdf"],
                    "latest_capability_results": [],
                    "latest_retrieval_summary": "",
                    "unresolved_items": [],
                },
                "episodic_summary": {"key_facts": ["release freeze exists"]},
                "selected_memory_ids": [recently_used.memory_id],
                "checkpoint_meta": {"updated_at": "2026-04-09T10:05:00Z", "run_status": "fresh"},
            },
        )

        self.assertIn("Release freeze fact", assembly.semantic_block)
        self.assertIn("Fallback grounded workflow", assembly.procedural_block)
        self.assertNotIn("Recently used grounded workflow", assembly.procedural_block)
        self.assertNotIn("Artifact map only", assembly.semantic_block)
        self.assertNotIn("Artifact map only", assembly.procedural_block)


if __name__ == "__main__":
    unittest.main()
