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

from src.backend.context.episodic_memory import build_episodic_summary
from src.backend.context.governance import extract_memory_candidates
from src.backend.context.recall import conversation_recall
from src.backend.context.store import context_store
from src.backend.context.working_memory import build_working_memory


class MemoryProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmpdir.name) / "backend"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        context_store.configure_for_base_dir(self.base_dir)

    def tearDown(self) -> None:
        context_store.close()
        self._tmpdir.cleanup()

    def test_memory_candidates_and_conversation_chunks_record_turn_provenance(self) -> None:
        state = {
            "thread_id": "thread-prov",
            "session_id": "session-prov",
            "run_id": "run-prov",
            "turn_id": "run-prov:0",
            "user_message": "Please remember that I prefer concise answers and our deployment target is Render.",
            "history": [
                {"role": "user", "content": "Please remember that I prefer concise answers."},
                {"role": "assistant", "content": "Noted."},
            ],
            "selected_memory_ids": ["mem-existing"],
            "checkpoint_meta": {"updated_at": "2026-04-09T12:00:00Z"},
            "final_answer": "I will keep answers concise and target Render for deployment.",
            "path_kind": "direct_answer",
        }
        working_memory = build_working_memory(state, updated_at="2026-04-09T12:00:00Z")
        episodic_summary = build_episodic_summary(state, updated_at="2026-04-09T12:00:00Z")

        candidates = extract_memory_candidates(
            state=state,
            working_memory=working_memory,
            episodic_summary=episodic_summary,
            base_dir=self.base_dir,
            updated_at="2026-04-09T12:00:00Z",
        )

        self.assertTrue(candidates)
        self.assertTrue(all(candidate.source_turn_ids == ("run-prov:0",) for candidate in candidates))
        self.assertTrue(all(candidate.source_run_ids == ("run-prov",) for candidate in candidates))
        self.assertTrue(all(candidate.generated_at == "2026-04-09T12:00:00Z" for candidate in candidates))
        self.assertTrue(any(candidate.source_memory_ids == ("mem-existing",) for candidate in candidates))

        stored = [context_store.insert_memory_candidate(candidate) for candidate in candidates]
        matched = context_store.list_memories_by_provenance(turn_id="run-prov:0", run_id="run-prov")
        chunks = conversation_recall.record(state=state, updated_at="2026-04-09T12:00:00Z")

        self.assertEqual({item.memory_id for item in matched}, {item.memory_id for item in stored})
        self.assertTrue(all("run-prov:0" in item.source_turn_ids for item in matched))
        self.assertTrue(chunks)
        self.assertTrue(all("run-prov:0" in item.source_turn_ids for item in chunks))
        self.assertTrue(all(item.generated_by == "conversation_recall.record" for item in chunks))


if __name__ == "__main__":
    unittest.main()
