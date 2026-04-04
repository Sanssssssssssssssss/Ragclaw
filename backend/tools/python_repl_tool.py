from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Type

from langchain_core.callbacks.manager import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from harness.capability_types import CapabilityResult

PYTHON_REPL_PREAMBLE = """from pathlib import Path
import json
import math
import statistics
import subprocess
import sys
import types

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import pypdf  # type: ignore
except ImportError:
    try:
        from PyPDF2 import PdfReader as _CompatPdfReader  # type: ignore

        pypdf = types.ModuleType("pypdf")
        pypdf.PdfReader = _CompatPdfReader
        sys.modules["pypdf"] = pypdf
    except ImportError:
        pypdf = None

try:
    import pdfplumber  # type: ignore
except ImportError:
    _fallback_pdf_reader = None
    if pypdf is not None:
        _fallback_pdf_reader = pypdf.PdfReader

    if _fallback_pdf_reader is not None:
        class _CompatPdfPlumberPage:
            def __init__(self, page):
                self._page = page

            def extract_text(self):
                return self._page.extract_text()

            def extract_tables(self):
                return []

        class _CompatPdfPlumberDocument:
            def __init__(self, path):
                self._reader = _fallback_pdf_reader(str(path))
                self.pages = [_CompatPdfPlumberPage(page) for page in self._reader.pages]

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        pdfplumber = types.ModuleType("pdfplumber")
        pdfplumber.open = lambda path: _CompatPdfPlumberDocument(path)
        sys.modules["pdfplumber"] = pdfplumber
    else:
        pdfplumber = None

_original_subprocess_run = subprocess.run

def _compat_subprocess_run(*popenargs, **kwargs):
    if popenargs:
        command = popenargs[0]
    else:
        command = kwargs.get("args")

    if isinstance(command, list) and command and command[0] in {"pip", "pip3"}:
        command = [sys.executable, "-m", "pip", *command[1:]]
        if popenargs:
            popenargs = (command, *popenargs[1:])
        else:
            kwargs["args"] = command
    elif isinstance(command, str):
        stripped = command.strip()
        if stripped.startswith("pip "):
            command = f"{sys.executable} -m pip {stripped[4:]}"
            if popenargs:
                popenargs = (command, *popenargs[1:])
            else:
                kwargs["args"] = command
        elif stripped.startswith("pip3 "):
            command = f"{sys.executable} -m pip {stripped[5:]}"
            if popenargs:
                popenargs = (command, *popenargs[1:])
            else:
                kwargs["args"] = command

    return _original_subprocess_run(*popenargs, **kwargs)

subprocess.run = _compat_subprocess_run
"""


class PythonReplInput(BaseModel):
    """Returns one Python code string from input data and validates tool execution payloads."""

    code: str = Field(..., description="Python code to execute")


class PythonReplTool(BaseTool):
    """Returns execution text from one code string input and runs short Python snippets for analysis tasks."""

    name: str = "python_repl"
    description: str = (
        "Execute short self-contained Python snippets in a fresh subprocess and return compact stdout/stderr. "
        "Each call is stateless, while common imports like Path and pandas as pd are preloaded when available."
    )
    args_schema: Type[BaseModel] = PythonReplInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        """Returns no value from one root directory path input and initializes the Python REPL tool workspace."""

        super().__init__(**kwargs)
        self._root_dir = root_dir

    def _build_wrapped_code(self, code: str) -> str:
        """Returns one executable Python script from a code string input and prepends the shared REPL preamble."""

        return f"{PYTHON_REPL_PREAMBLE}\n\n{code}"

    def _format_error_output(self, raw_output: str) -> str:
        """Returns one compact error string from raw stderr input and translates common REPL failures into guidance."""

        if "No module named 'openpyxl'" in raw_output or "Import openpyxl failed" in raw_output:
            return (
                "Python REPL error: reading .xlsx files requires `openpyxl` in the backend environment. "
                "Install it with `backend/.venv/Scripts/python.exe -m pip install openpyxl`, then rerun the same snippet."
            )

        if "UnicodeEncodeError" in raw_output and "gbk" in raw_output.lower():
            return (
                "Python REPL error: Windows console encoding rejected part of the snippet output. "
                "Rerun with shorter printed text or save the result to a UTF-8 file inside the same snippet before reading it back."
            )

        if "No module named 'pdfplumber'" in raw_output:
            return (
                "Python REPL error: `pdfplumber` is unavailable in the current backend environment. "
                "Use the built-in PDF compatibility layer or switch to `from pypdf import PdfReader` in the same snippet."
            )

        if "No module named 'pypdf'" in raw_output:
            return (
                "Python REPL error: `pypdf` is unavailable as a direct package name, but the tool now exposes a compatibility alias "
                "when `PyPDF2` is installed. Rerun the same snippet once without manual package-install logic."
            )

        if "FileNotFoundError" in raw_output and ("'pip'" in raw_output or '"pip"' in raw_output):
            return (
                "Python REPL error: direct `pip` invocation is unavailable in this environment. "
                "Use `python -m pip ...` instead, or rely on the tool's automatic pip normalization."
            )

        match = re.search(r"NameError: name '([^']+)' is not defined", raw_output)
        if match:
            variable = match.group(1)
            if variable == "df":
                return (
                    "Python REPL error: `df` is undefined in this snippet. "
                    "Each `python_repl` call runs in a fresh process, so reload the dataframe in the same snippet before using `df`."
                )
            if variable == "pd":
                return (
                    "Python REPL error: `pd` is unavailable in this snippet. "
                    "The tool preloads `pandas as pd` when pandas is installed; if this persists, reinstall backend dependencies and rerun."
                )
            return (
                f"Python REPL error: `{variable}` is undefined in this snippet. "
                "Each `python_repl` call is stateless, so recreate imports and variables in the same execution."
            )

        lines = [line for line in raw_output.strip().splitlines() if line.strip()]
        if not lines:
            return "[no output]"
        return "\n".join(lines[-8:])[:5000]

    def _run(
        self,
        code: str,
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        """Returns execution output from one code string input and runs the snippet inside the backend workspace."""

        return self.render_capability_result(self.execute_capability({"code": code}))

    def execute_capability(self, payload: dict[str, str]) -> CapabilityResult:
        code = str(payload.get("code", "") or "")

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", "-c", self._build_wrapped_code(code)],
                cwd=self._root_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CapabilityResult(
                status="failed",
                payload={},
                partial=False,
                error_type="timeout",
                error_message="Timed out after 15 seconds.",
                retryable=False,
            )
        combined = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode != 0:
            return CapabilityResult(
                status="failed",
                payload={},
                partial=False,
                error_type="execution_error",
                error_message=self._format_error_output(combined),
                retryable=False,
            )
        return CapabilityResult(
            status="success",
            payload={"text": (combined.strip() or "[no output]")[:5000]},
            partial=False,
        )

    def render_capability_result(self, result: CapabilityResult) -> str:
        if result.payload.get("text"):
            return str(result.payload.get("text", ""))
        return result.error_message or "[no output]"

    async def _arun(
        self,
        code: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Returns async execution output from one code string input and delegates the sync REPL runner to a worker thread."""

        result = await self.aexecute_capability({"code": code})
        return self.render_capability_result(result)

    async def aexecute_capability(self, payload: dict[str, str]) -> CapabilityResult:
        return await asyncio.to_thread(self.execute_capability, payload)
