from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from benchmarks.run_harness_live_validation import DEFAULT_CASES, _parse_sse_payload, run_live_validation


class HarnessLiveValidationTests(unittest.IsolatedAsyncioTestCase):
    def test_live_case_file_loads_expected_categories(self) -> None:
        case_ids = {case.case_id for case in DEFAULT_CASES}
        self.assertIn("live_direct_answer", case_ids)
        self.assertIn("live_multi_tool_same_name", case_ids)
        self.assertIn("live_queue_with_failure", case_ids)
        self.assertIn("live_mcp_filesystem_read", case_ids)
        self.assertIn("live_mcp_web_fetch", case_ids)
        self.assertIn("live_resume_direct_answer", case_ids)
        self.assertIn("live_hitl_python_repl_approve", case_ids)

    def test_parse_sse_payload_recovers_event_sequence(self) -> None:
        raw = (
            'event: run.queued\n'
            'data: {"session_id":"s-1"}\n\n'
            'event: token\n'
            'data: {"content":"hello"}\n\n'
            'event: done\n'
            'data: {"content":"hello"}\n\n'
        )
        parsed = _parse_sse_payload(raw)
        self.assertEqual([name for name, _payload in parsed], ["run.queued", "token", "done"])

    async def test_live_validation_direct_case_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live_validation.json"
            payload = await run_live_validation(
                case_ids=["live_direct_answer"],
                output_path=output_path,
            )
            self.assertTrue(output_path.exists())
            stored = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["summary"]["total_cases"], 1)
            self.assertEqual(stored["summary"]["passed_cases"], 1)
            self.assertEqual(payload["cases"][0]["case_id"], "live_direct_answer")

    async def test_live_validation_queue_case_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live_validation_queue.json"
            payload = await run_live_validation(
                case_ids=["live_same_session_queue"],
                output_path=output_path,
            )
            self.assertTrue(output_path.exists())
            self.assertEqual(payload["cases"][0]["case_id"], "live_same_session_queue")
            self.assertTrue(payload["cases"][0]["queued"])

    async def test_live_validation_mcp_case_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live_validation_mcp.json"
            payload = await run_live_validation(
                case_ids=["live_mcp_filesystem_read"],
                output_path=output_path,
            )
            self.assertTrue(output_path.exists())
            self.assertEqual(payload["cases"][0]["case_id"], "live_mcp_filesystem_read")
            self.assertTrue(payload["cases"][0]["capability_present"])

    async def test_live_validation_web_mcp_case_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live_validation_web_mcp.json"
            payload = await run_live_validation(
                case_ids=["live_mcp_web_fetch"],
                output_path=output_path,
            )
            self.assertTrue(output_path.exists())
            self.assertEqual(payload["cases"][0]["case_id"], "live_mcp_web_fetch")
            self.assertTrue(payload["cases"][0]["capability_present"])

    async def test_live_validation_resume_case_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live_validation_resume.json"
            payload = await run_live_validation(
                case_ids=["live_resume_direct_answer"],
                output_path=output_path,
            )
            self.assertTrue(output_path.exists())
            self.assertEqual(payload["cases"][0]["case_id"], "live_resume_direct_answer")
            self.assertTrue(payload["cases"][0]["resume_present"])

    async def test_live_validation_hitl_case_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "live_validation_hitl.json"
            payload = await run_live_validation(
                case_ids=["live_hitl_python_repl_approve"],
                output_path=output_path,
            )
            self.assertTrue(output_path.exists())
            self.assertEqual(payload["cases"][0]["case_id"], "live_hitl_python_repl_approve")
            self.assertTrue(payload["cases"][0]["resume_present"])
            self.assertTrue(payload["cases"][0]["capability_present"])


if __name__ == "__main__":
    unittest.main()
