from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from benchmarks.run_harness_live_validation import _parse_sse_payload, run_live_validation


class HarnessLiveValidationTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
