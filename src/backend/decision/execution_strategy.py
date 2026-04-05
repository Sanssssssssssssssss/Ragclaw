from __future__ import annotations

import re
from dataclasses import dataclass, field


TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    "terminal": ("terminal", "shell", "终端", "命令行"),
    "python_repl": ("python_repl", "python repl", "python", "python工具"),
    "read_file": ("read_file", "read file", "读取文件", "读文件"),
    "fetch_url": ("fetch_url", "fetch url", "抓取网页", "网页抓取", "url"),
}

NEGATIVE_CLAUSE_PATTERN = re.compile(
    r"(?:不要|别|请勿|禁止|do not|don't|never)\s*(?:使用|调用|读取|进行)?\s*([^。！？?\n，,；;]*)",
    re.IGNORECASE,
)
ONLY_USE_CLAUSE_PATTERN = re.compile(
    r"(?:只使用|仅使用|只能使用|只允许使用|仅允许使用|only use)\s*([^。！？?\n，,；;]*)",
    re.IGNORECASE,
)

NO_TOOL_PATTERNS = (
    re.compile(r"(?:不要|别|请勿|禁止).{0,8}(?:任何|所有)?工具"),
    re.compile(r"(?:do not|don't|never)\s+(?:use|call)\s+(?:any|all)\s+tools?", re.IGNORECASE),
)
NO_KNOWLEDGE_PATTERNS = (
    re.compile(r"(?:不要|别|请勿|禁止).{0,8}(?:读取|使用|走|进入|触发)?.{0,10}(?:知识库|知识检索|知识库检索)"),
    re.compile(
        r"(?:do not|don't|never)\s+(?:read|use|trigger|enter)\s+(?:the\s+)?knowledge(?:\s+base|\s+retrieval)?",
        re.IGNORECASE,
    ),
)
NO_RETRIEVAL_PATTERNS = (
    re.compile(r"(?:不要|别|请勿|禁止).{0,8}(?:检索|搜索|召回)"),
    re.compile(r"(?:do not|don't|never)\s+(?:retrieve|search)", re.IGNORECASE),
)
DIRECT_ANSWER_PATTERNS = (
    re.compile(r"(?:直接回答|直接给出答案|直接说结论|直接用.*常识|用你自己的常识(?:回答)?|凭常识(?:回答)?)"),
    re.compile(
        r"(?:answer directly|answer from your own knowledge|use your own common sense|without tools|no tools needed)",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class ExecutionStrategy:
    allow_tools: bool = True
    allow_knowledge: bool = True
    allow_retrieval: bool = True
    force_direct_answer: bool = False
    require_tool_use: bool = False
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    blocked_tools: frozenset[str] = field(default_factory=frozenset)

    def to_instructions(self) -> list[str]:
        instructions: list[str] = []
        if not self.allow_knowledge:
            instructions.append(
                "Do not use knowledge-base retrieval, skill retrieval, vector retrieval, BM25 retrieval, or any fallback retrieval."
            )
        if not self.allow_retrieval:
            instructions.append("Do not perform any retrieval step for this request.")
        if not self.allow_tools:
            instructions.append("Do not call any tools for this request. Answer directly.")
        if self.allowed_tools:
            instructions.append(
                "Only these tools are allowed for this request: "
                + ", ".join(sorted(self.allowed_tools))
                + "."
            )
        if self.blocked_tools:
            instructions.append(
                "Do not call these tools for this request: "
                + ", ".join(sorted(self.blocked_tools))
                + "."
            )
        if self.require_tool_use and self.allowed_tools:
            instructions.append("You must use at least one allowed tool before producing the final answer.")
        if self.force_direct_answer:
            instructions.append("Provide a direct final answer in natural language instead of delegating to tools or retrieval.")
        return instructions


def _normalize_message(message: str) -> str:
    return message.strip().lower()


def _extract_tools_from_clause(clause: str) -> set[str]:
    matched: set[str] = set()
    for tool_name, aliases in TOOL_ALIASES.items():
        if any(alias.lower() in clause for alias in aliases):
            matched.add(tool_name)
    return matched


def parse_execution_strategy(message: str) -> ExecutionStrategy:
    normalized = _normalize_message(message)
    allow_tools = True
    allow_knowledge = True
    allow_retrieval = True
    force_direct_answer = False
    require_tool_use = False
    allowed_tools: set[str] = set()
    blocked_tools: set[str] = set()

    if any(pattern.search(normalized) for pattern in NO_TOOL_PATTERNS):
        allow_tools = False

    if any(pattern.search(normalized) for pattern in NO_KNOWLEDGE_PATTERNS):
        allow_knowledge = False

    if any(pattern.search(normalized) for pattern in NO_RETRIEVAL_PATTERNS):
        allow_retrieval = False

    if any(pattern.search(normalized) for pattern in DIRECT_ANSWER_PATTERNS):
        force_direct_answer = True

    for match in NEGATIVE_CLAUSE_PATTERN.finditer(normalized):
        blocked_tools.update(_extract_tools_from_clause(match.group(1)))

    for match in ONLY_USE_CLAUSE_PATTERN.finditer(normalized):
        clause_tools = _extract_tools_from_clause(match.group(1))
        if clause_tools:
            allowed_tools.update(clause_tools)
            require_tool_use = True

    if allowed_tools:
        allow_tools = True
        blocked_tools.difference_update(allowed_tools)

    if not allow_knowledge:
        allow_retrieval = False

    if force_direct_answer and not allowed_tools:
        allow_tools = False
        allow_retrieval = False

    return ExecutionStrategy(
        allow_tools=allow_tools,
        allow_knowledge=allow_knowledge,
        allow_retrieval=allow_retrieval,
        force_direct_answer=force_direct_answer,
        require_tool_use=require_tool_use,
        allowed_tools=frozenset(allowed_tools),
        blocked_tools=frozenset(blocked_tools),
    )
