from __future__ import annotations

import asyncio
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Type

from langchain_core.callbacks.manager import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from config import get_settings, runtime_config


BLOCKED_PATTERNS = (
    "rm -rf /",
    "shutdown",
    "reboot",
    "mkfs",
    "format ",
    ":(){:|:&};:",
)


class TerminalToolInput(BaseModel):
    command: str = Field(..., description="Shell command to execute inside the project root")


class TerminalTool(BaseTool):
    name: str = "terminal"
    description: str = (
        "Execute shell commands inside the project root. Use this for quick inspection, "
        "building, or local commands. Dangerous system-destructive commands are blocked."
    )
    args_schema: Type[BaseModel] = TerminalToolInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir

    def _quote_powershell(self, value: str) -> str:
        """Returns one PowerShell-safe single-quoted string from a text input and escapes embedded apostrophes."""

        return "'" + value.replace("'", "''") + "'"

    def _strip_shell_quotes(self, value: str) -> str:
        """Returns one plain text string from a shell token input and removes one matching layer of outer quotes."""

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]
        return value

    def _normalize_windows_command(self, command: str) -> str:
        """Returns one Windows PowerShell command from a shell-like command input and rewrites common bash/cmd idioms."""

        stripped = command.strip()
        if not stripped:
            return stripped

        pip_match = re.fullmatch(r"pip3?\s+(.+)", stripped)
        if pip_match:
            return f"python -m pip {pip_match.group(1)}"

        pdftotext_match = re.fullmatch(
            r'pdftotext(?:\s+(-layout))?(?:\s+-f\s+(\d+))?(?:\s+-l\s+(\d+))?\s+(".*?"|\'.*?\'|\S+)\s+(".*?"|\'.*?\'|\S+)',
            stripped,
        )
        if pdftotext_match:
            layout_flag, first_page, last_page, input_pdf, output_txt = pdftotext_match.groups()
            parts = ["python", "scripts/pdf_extract_text.py"]
            if layout_flag:
                parts.append("-layout")
            if first_page:
                parts.extend(["-f", first_page])
            if last_page:
                parts.extend(["-l", last_page])
            parts.append(self._quote_powershell(self._strip_shell_quotes(input_pdf)))
            parts.append(self._quote_powershell(self._strip_shell_quotes(output_txt)))
            return " ".join(parts)

        directory_test_match = re.fullmatch(
            r'test\s+-d\s+(".*?"|\'.*?\'|\S+)\s+&&\s+echo\s+"([^"]*)"\s+\|\|\s+echo\s+"([^"]*)"',
            stripped,
        )
        if directory_test_match:
            raw_path, success_message, failure_message = directory_test_match.groups()
            path_value = raw_path.strip("\"'")
            return (
                f"if (Test-Path -Path {self._quote_powershell(path_value)} -PathType Container) "
                f"{{ Write-Output {self._quote_powershell(success_message)} }} "
                f"else {{ Write-Output {self._quote_powershell(failure_message)} }}"
            )

        ls_match = re.fullmatch(r"ls\s+-la(?:\s+(.*))?", stripped)
        if ls_match:
            target = (ls_match.group(1) or ".").strip()
            return f"Get-ChildItem -Force -LiteralPath {self._quote_powershell(self._strip_shell_quotes(target))}"

        findstr_match = re.fullmatch(
            r'findstr\s+/i\s+/n\s+"([^"]+)"\s+(.+?)(?:\s+2>nul)?(?:\s+\|\s+head\s*-(\d+))?\s*$',
            stripped,
        )
        if findstr_match:
            pattern, raw_paths, head_limit = findstr_match.groups()
            quoted_paths = re.findall(r'"([^"]+)"', raw_paths)
            if quoted_paths:
                file_list = ", ".join(self._quote_powershell(path) for path in quoted_paths)
                limit = int(head_limit) if head_limit else 100
                return (
                    f"$matches = Get-ChildItem -Path {file_list} -File -ErrorAction SilentlyContinue | "
                    f"Select-String -Pattern {self._quote_powershell(pattern)} | "
                    f"Select-Object -First {limit}; "
                    "if (-not $matches) { Write-Output '[no output]' } "
                    "else { $matches | ForEach-Object { '{0}:{1}:{2}' -f $_.Path, $_.LineNumber, $_.Line.TrimEnd() } }"
                )

        stripped = re.sub(
            r"\s+\|\s+head\s*-(\d+)\s*$",
            lambda match: f" | Select-Object -First {match.group(1)}",
            stripped,
        )
        stripped = stripped.replace("2>nul", "2>$null")
        stripped = stripped.replace("/workspace/", "./")
        return stripped

    def _normalize_linux_command(self, command: str) -> str:
        """Returns one Linux bash command from a shell-like command input and lightly rewrites common workspace aliases."""

        stripped = command.strip()
        if not stripped:
            return stripped
        pip_match = re.fullmatch(r"pip3?\s+(.+)", stripped)
        if pip_match:
            return f"python -m pip {pip_match.group(1)}"
        pdftotext_match = re.fullmatch(
            r'pdftotext(?:\s+(-layout))?(?:\s+-f\s+(\d+))?(?:\s+-l\s+(\d+))?\s+(".*?"|\'.*?\'|\S+)\s+(".*?"|\'.*?\'|\S+)',
            stripped,
        )
        if pdftotext_match:
            layout_flag, first_page, last_page, input_pdf, output_txt = pdftotext_match.groups()
            parts = ["python", "scripts/pdf_extract_text.py"]
            if layout_flag:
                parts.append("-layout")
            if first_page:
                parts.extend(["-f", first_page])
            if last_page:
                parts.extend(["-l", last_page])
            parts.append(self._strip_shell_quotes(input_pdf))
            parts.append(self._strip_shell_quotes(output_txt))
            return " ".join(parts)
        return stripped.replace("\\workspace\\", "./").replace("/workspace/", "./")

    def _get_execution_platform(self) -> str:
        """Return one effective execution-platform label from runtime config and host fallback."""

        configured = runtime_config.get_execution_platform()
        if configured in {"windows", "linux"}:
            return configured
        return "windows" if platform.system().lower().startswith("win") else "linux"

    def _build_shell_command(self, execution_platform: str, command: str) -> list[str] | None:
        """Return one shell launcher command from platform and shell text inputs."""

        if execution_platform == "windows":
            powershell_path = shutil.which("powershell") or shutil.which("pwsh")
            if powershell_path:
                return [powershell_path, "-NoProfile", "-Command", command]
            return None

        bash_path = shutil.which("bash")
        if bash_path:
            return [bash_path, "-lc", command]
        return None

    def _run(
        self,
        command: str,
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        lowered = command.lower()
        if any(pattern in lowered for pattern in BLOCKED_PATTERNS):
            return "Blocked: command matches the terminal blacklist."

        settings = get_settings()
        execution_platform = self._get_execution_platform()
        normalized_command = (
            self._normalize_windows_command(command)
            if execution_platform == "windows"
            else self._normalize_linux_command(command)
        )
        shell_command = self._build_shell_command(execution_platform, normalized_command)
        if shell_command is None:
            return (
                f"Configured execution platform is {execution_platform}, "
                f"but the required shell is not available on this machine."
            )
        try:
            completed = subprocess.run(
                shell_command,
                cwd=self._root_dir,
                capture_output=True,
                text=True,
                timeout=settings.terminal_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "Timed out after 30 seconds."

        combined = (completed.stdout or "") + (completed.stderr or "")
        combined = combined.strip() or "[no output]"
        return combined[:5000]

    async def _arun(
        self,
        command: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        return await asyncio.to_thread(self._run, command, None)
