from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.api import app as backend_app


class ApiStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_schedules_knowledge_warm_start_without_awaiting_it(self) -> None:
        with (
            patch.object(backend_app, "refresh_snapshot"),
            patch.object(backend_app.agent_manager, "initialize"),
            patch.object(backend_app.memory_indexer, "configure"),
            patch.object(backend_app.memory_indexer, "rebuild_index"),
            patch.object(backend_app.knowledge_indexer, "configure"),
            patch.object(backend_app, "_schedule_knowledge_warm_start") as schedule_mock,
        ):
            async with backend_app.lifespan(backend_app.app):
                pass

        schedule_mock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
