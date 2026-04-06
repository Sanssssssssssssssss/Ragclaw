from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent


class RuntimeImportTests(unittest.TestCase):
    def test_graders_import_from_project_root_only(self) -> None:
        original_sys_path = list(sys.path)
        module_name = "src.backend.runtime.graders"
        try:
            sys.path[:] = [str(PROJECT_ROOT)]
            sys.modules.pop(module_name, None)
            module = importlib.import_module(module_name)
            self.assertTrue(hasattr(module, "KnowledgeAnswerGrader"))
            self.assertTrue(hasattr(module, "load_judge_client"))
        finally:
            sys.path[:] = original_sys_path
            sys.modules.pop(module_name, None)


if __name__ == "__main__":
    unittest.main()
