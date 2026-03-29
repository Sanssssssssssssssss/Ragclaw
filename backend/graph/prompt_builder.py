from __future__ import annotations

from pathlib import Path

from config import get_settings, runtime_config

SYSTEM_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("Skills Snapshot", "SKILLS_SNAPSHOT.md"),
    ("Soul", "workspace/SOUL.md"),
    ("Identity", "workspace/IDENTITY.md"),
    ("User Profile", "workspace/USER.md"),
    ("Agents Guide", "workspace/AGENTS.md"),
    ("Long-term Memory", "memory/MEMORY.md"),
)

def _build_runtime_override(execution_platform: str) -> str:
    """Return one runtime guidance block from the configured execution platform."""

    if execution_platform == "linux":
        terminal_guidance = (
            "When using terminal, this environment is Linux bash. "
            "Prefer `pwd`, `ls -la`, `find`, `grep`, `head`, `test -d`, `python -m pip`, and shell chaining such as `&&`."
        )
    else:
        terminal_guidance = (
            "When using terminal, this environment is Windows PowerShell. "
            "Prefer `Get-ChildItem`, `Test-Path`, `Select-String`, `Select-Object`, `python -m pip`, and PowerShell control flow "
            "over bash/cmd syntax such as `ls -la`, `test -d`, `&&`, `||`, or `head -100`."
        )

    return (
        "<!-- Runtime Override -->\n"
        "When explicit retrieval evidence is provided for the current request, prioritize that evidence.\n"
        "Do not assume missing evidence exists elsewhere.\n"
        f"{terminal_guidance}\n"
        "When using python_repl, treat every execution as stateless and include any required imports, dataframe loading, and variable setup in the same snippet.\n"
    )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _read_component(base_dir: Path, relative_path: str, limit: int) -> str:
    path = base_dir / relative_path
    if not path.exists():
        return f"[missing component: {relative_path}]"
    return _truncate(path.read_text(encoding="utf-8"), limit)


def build_system_prompt(base_dir: Path, rag_mode: bool) -> str:
    settings = get_settings()
    execution_platform = runtime_config.get_execution_platform()
    parts: list[str] = []

    for label, relative_path in SYSTEM_COMPONENTS:
        if rag_mode and relative_path == "memory/MEMORY.md":
            parts.append(
                "<!-- Long-term Memory -->\n"
                "长期记忆将通过检索动态注入。你应优先使用当次检索到的 MEMORY 片段，"
                "不要假设未检索到的记忆仍然有效。"
            )
            continue

        content = _read_component(base_dir, relative_path, settings.component_char_limit)
        parts.append(f"<!-- {label} -->\n{content}")

    parts.append(_build_runtime_override(execution_platform))
    return "\n\n".join(parts)
