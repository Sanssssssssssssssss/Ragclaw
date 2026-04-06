from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from src.backend.runtime.config import get_settings
from src.backend.runtime.token_utils import count_tokens


ROUTER_INTENTS = {
    "direct_answer",
    "knowledge_qa",
    "workspace_file_ops",
    "computation_or_transformation",
    "web_lookup",
}

WORKSPACE_SUBTYPES = {
    "read_existing_file",
    "search_workspace_file",
    "modify_or_run_in_workspace",
}

COMPUTE_SUBTYPES = {
    "pure_calculation",
    "file_backed_calculation",
    "pure_text_transformation",
    "code_execution_request",
}

ALL_SUBTYPES = WORKSPACE_SUBTYPES | COMPUTE_SUBTYPES

ROUTER_CAPABILITY_GUIDE = """Intent guide:
- direct_answer: explanation, summarization, translation, rewriting, or simple arithmetic that does not need external state.
- knowledge_qa: indexed knowledge-base questions, report/source lookup, grounded report comparison, or requests that explicitly depend on retrieval evidence.
- workspace_file_ops: local repo or workspace files, path search, reading a known file, or modifying/running something in the workspace.
- computation_or_transformation: structured calculation, parsing, code execution, or file-backed analysis.
- web_lookup: live/current online facts, official docs, links, news, pricing, or weather.

Decision hints:
- Prefer knowledge_qa when the user asks for a report, source path, cited evidence, or grounded comparison.
- Prefer workspace_file_ops only when there is a clear local/workspace anchor.
- Prefer direct_answer when no external state is needed.
- Prefer web_lookup only for live/current online facts or weather.

Tool guide:
- read_file: known file content only.
- mcp_filesystem_read_file: read one known local file through the read-only Filesystem MCP path.
- mcp_filesystem_list_directory: list one known local directory through the read-only Filesystem MCP path.
- mcp_web_fetch_url: fetch one public URL through the read-only Web MCP path when the user explicitly asks for Web MCP.
- terminal: search/list workspace files or run workspace commands.
- python_repl: structured computation, parsing, transforms, or code execution.
- fetch_url: live web pages, links, docs, and weather.

Rules:
- Prefer the narrowest route and smallest tool set that can solve the request.
- If the request is ambiguous, lower confidence instead of inventing certainty.
- Do not skip retrieval when the user asks for grounded evidence or report/source lookup.
"""

WEB_LOOKUP_PATTERNS = (
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"\b(url|website|webpage|web|online|internet|look up|search online)\b", re.IGNORECASE),
    re.compile(r"(官网|网页|网址|页面|抓取网页|网页内容|联网|上网|查网页|搜索网页|在线资料)"),
)

WORKSPACE_PATH_PATTERNS = (
    re.compile(r"(knowledge/|workspace/|memory/|storage/|backend/)", re.IGNORECASE),
    re.compile(r"\b(repo|repository|workspace|backend|local file|local folder)\b", re.IGNORECASE),
    re.compile(r"(本地|工作区|仓库|目录|文件夹|文件路径)"),
)

READ_FILE_PATTERNS = (
    re.compile(r"\b(read|open|show|view)\b", re.IGNORECASE),
    re.compile(r"(读取|打开|查看|看看|显示|看下|看一眼)"),
)

MCP_FILESYSTEM_PATTERNS = (
    re.compile(r"\bfilesystem mcp\b", re.IGNORECASE),
    re.compile(r"\bmcp filesystem\b", re.IGNORECASE),
    re.compile(r"\bmcp\b.{0,20}\b(file|directory|folder|path|workspace)\b", re.IGNORECASE),
)

MCP_WEB_PATTERNS = (
    re.compile(r"\bweb mcp\b", re.IGNORECASE),
    re.compile(r"\bdocument fetch mcp\b", re.IGNORECASE),
    re.compile(r"\bmcp\b.{0,20}\b(url|web|website|webpage|document|page)\b", re.IGNORECASE),
)

SEARCH_FILE_PATTERNS = (
    re.compile(r"\b(list|find|search|locate|where is|which file|what file)\b", re.IGNORECASE),
    re.compile(r"\b(files?\s+under|under\s+backend/|under\s+workspace/|under\s+knowledge/)\b", re.IGNORECASE),
    re.compile(r"(列出|查找|搜索|定位|在哪|哪个文件|哪份文件|哪份报告|来源路径)"),
)

MODIFY_OR_RUN_PATTERNS = (
    re.compile(r"\b(run|execute|edit|modify|change|patch|update|create|delete)\b", re.IGNORECASE),
    re.compile(r"(运行|执行|修改|编辑|更新|创建|删除|改一下|改动)"),
)

FILE_BACKED_PATTERNS = (
    re.compile(r"\.(json|csv|tsv|xlsx|xls|md|txt|py)\b", re.IGNORECASE),
    re.compile(r"(faq|config|sales_orders|customers|json|xlsx|csv)", re.IGNORECASE),
    re.compile(r"(记录|字段|列名|行数|文件里|文件中|表格里)"),
)

COMPUTE_PATTERNS = (
    re.compile(r"\b(count|calculate|compute|transform|convert|parse|extract|stats?|dedupe)\b", re.IGNORECASE),
    re.compile(r"(统计|计算|转换|解析|提取|汇总|整理|去重|算一下|数一下)"),
)

DIRECT_PATTERNS = (
    re.compile(r"\b(explain|what is|why is|translate|rewrite|summarize)\b", re.IGNORECASE),
    re.compile(r"(解释|什么是|为什么|翻译|改写|总结|一句话概括)"),
)

KNOWLEDGE_PATTERNS = (
    re.compile(r"\bknowledge\b", re.IGNORECASE),
    re.compile(r"\b(retrieval|rag)\b", re.IGNORECASE),
    re.compile(r"(知识库|根据知识库|基于知识库|从知识库)"),
    re.compile(r"(哪份|哪个|哪张|那个|那份).{0,30}(文档|文件|报告|财报|路径|来源)"),
    re.compile(r"(给出|返回).{0,12}(路径|来源)"),
    re.compile(r"\b(which|what)\b.{0,24}\b(file|document|report|path|source)\b", re.IGNORECASE),
)

AMBIGUOUS_PATTERNS = (
    ("ambiguous_language", re.compile(r"(那个|那份|之前|刚才|帮我看看|顺便|顺手|大概|类似|那个材料|那个东西)")),
    ("ambiguous_language", re.compile(r"\b(that one|previous|roughly|kind of|sort of|take a look|while you're at it)\b", re.IGNORECASE)),
    ("fuzzy_document_seeking", re.compile(r"(材料|报告|文档|文件).{0,20}(路径|来源)?")),
    ("mixed_intent", re.compile(r"(顺便|同时|另外|再|并且|然后)")),
    ("mixed_intent", re.compile(r"\b(and also|also|plus|while you're at it|then)\b", re.IGNORECASE)),
)


@dataclass(frozen=True)
class RoutingDecision:
    intent: str
    needs_tools: bool
    needs_retrieval: bool
    allowed_tools: tuple[str, ...]
    confidence: float
    reason_short: str
    source: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    ambiguity_flags: tuple[str, ...] = ()
    escalated: bool = False
    model_name: str = ""
    subtype: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "needs_tools": self.needs_tools,
            "needs_retrieval": self.needs_retrieval,
            "allowed_tools": list(self.allowed_tools),
            "confidence": self.confidence,
            "reason_short": self.reason_short,
            "source": self.source,
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "ambiguity_flags": list(self.ambiguity_flags),
            "escalated": self.escalated,
            "model_name": self.model_name,
            "subtype": self.subtype,
        }


def summarize_hard_constraints(strategy) -> str:
    parts: list[str] = []
    if not strategy.allow_tools:
        parts.append("no_tools")
    if not strategy.allow_knowledge:
        parts.append("no_knowledge")
    if not strategy.allow_retrieval:
        parts.append("no_retrieval")
    if strategy.force_direct_answer:
        parts.append("direct_answer")
    if strategy.allowed_tools:
        parts.append("only:" + ",".join(sorted(strategy.allowed_tools)))
    if strategy.blocked_tools:
        parts.append("blocked:" + ",".join(sorted(strategy.blocked_tools)))
    return "; ".join(parts) if parts else "none"


def _history_excerpt(history: list[dict[str, Any]]) -> str:
    compact: list[str] = []
    for item in history[-2:]:
        role = str(item.get("role", "")).strip()
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content", "") or "").strip().replace("\n", " ")
        if not content:
            continue
        compact.append(f"{role}: {content[:180]}")
    return "\n".join(compact) if compact else "none"


def _normalize_allowed_tools(tools: list[str] | tuple[str, ...], allowed_tool_names: set[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tool in tools:
        candidate = str(tool or "").strip()
        if candidate in allowed_tool_names and candidate not in seen:
            seen.add(candidate)
            normalized.append(candidate)
    return tuple(normalized)


def _intent_tools(intent: str, subtype: str, message: str, allowed_tool_names: set[str]) -> tuple[str, ...]:
    if any(pattern.search(str(message or "")) for pattern in MCP_WEB_PATTERNS):
        if intent == "web_lookup":
            selected = tuple(tool for tool in ("mcp_web_fetch_url",) if tool in allowed_tool_names)
            if selected:
                return selected
    if any(pattern.search(str(message or "")) for pattern in MCP_FILESYSTEM_PATTERNS):
        if intent == "workspace_file_ops":
            preferred = (
                ("mcp_filesystem_read_file",)
                if subtype == "read_existing_file"
                else ("mcp_filesystem_list_directory",)
            )
            selected = tuple(tool for tool in preferred if tool in allowed_tool_names)
            if selected:
                return selected
    if intent in {"direct_answer", "knowledge_qa"}:
        return ()
    if intent == "web_lookup":
        return tuple(tool for tool in ("fetch_url",) if tool in allowed_tool_names)
    if intent == "workspace_file_ops":
        if subtype == "read_existing_file":
            preferred = ("read_file",)
        elif subtype == "search_workspace_file":
            preferred = ("terminal",)
        else:
            preferred = ("terminal",)
        return tuple(tool for tool in preferred if tool in allowed_tool_names)
    if intent == "computation_or_transformation":
        if subtype == "pure_calculation":
            return ()
        if subtype == "file_backed_calculation":
            preferred = ("python_repl",)
        elif subtype == "pure_text_transformation":
            preferred = ()
        elif subtype == "code_execution_request":
            lowered = str(message or "").lower()
            preferred = ("terminal",) if any(term in lowered for term in ("bash", "powershell", "cmd", "shell")) else ("python_repl",)
        else:
            preferred = ("python_repl",)
        return tuple(tool for tool in preferred if tool in allowed_tool_names)
    return ()


def _workspace_subtype(message: str) -> str:
    normalized = str(message or "").strip()
    lowered = normalized.lower()
    if any(pattern.search(normalized) for pattern in MODIFY_OR_RUN_PATTERNS):
        return "modify_or_run_in_workspace"
    if any(pattern.search(normalized) for pattern in SEARCH_FILE_PATTERNS):
        return "search_workspace_file"
    if any(pattern.search(normalized) for pattern in READ_FILE_PATTERNS):
        return "read_existing_file"
    if any(term in lowered for term in ("repo", "workspace", "backend", "user.md", "config.py")):
        return "read_existing_file"
    return "search_workspace_file"


def _compute_subtype(message: str) -> str:
    normalized = str(message or "").strip()
    lowered = normalized.lower()
    if any(term in lowered for term in ("rewrite", "summarize", "translate", "改写", "总结", "翻译")):
        return "pure_text_transformation"
    if any(term in lowered for term in ("run this code", "execute this code", "运行这段代码", "执行这段代码", "run script", "execute script")):
        return "code_execution_request"
    if any(pattern.search(normalized) for pattern in FILE_BACKED_PATTERNS):
        return "file_backed_calculation"
    if re.search(r"\b\d+\s*[\+\-\*/]\s*\d+\b", normalized) or any(term in normalized for term in ("几加几", "几乘几", "简单算一下")):
        return "pure_calculation"
    return "pure_calculation"


def _is_workspace_request(message: str) -> bool:
    normalized = str(message or "").strip()
    if any(pattern.search(normalized) for pattern in WEB_LOOKUP_PATTERNS):
        return False
    if any(pattern.search(normalized) for pattern in WORKSPACE_PATH_PATTERNS):
        return True
    if any(pattern.search(normalized) for pattern in READ_FILE_PATTERNS) and re.search(
        r"\b(file|document|profile|config|readme)\b",
        normalized,
        re.IGNORECASE,
    ):
        return True
    return False


def _has_explicit_workspace_anchor(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    return bool(
        re.search(r"(knowledge/|workspace/|memory/|storage/|backend/)", normalized, re.IGNORECASE)
        or any(token in normalized for token in ("repo", "repository", "config.py", "readme", "user.md"))
        or any(token in message for token in ("工作区", "仓库", "本地文件", "后端代码"))
    )


def _is_knowledge_request(message: str) -> bool:
    normalized = str(message or "").strip()
    return any(pattern.search(normalized) for pattern in KNOWLEDGE_PATTERNS)


def _has_explicit_doc_seek(message: str) -> bool:
    normalized = str(message or "").strip()
    return bool(
        re.search(r"(给出|返回).{0,12}(路径|来源)", normalized)
        or re.search(r"(哪份|哪个|哪张|那个|那份).{0,30}(文档|文件|报告|财报|路径|来源)", normalized)
        or re.search(r"\b(which|what)\b.{0,24}\b(file|document|report|path|source)\b", normalized, re.IGNORECASE)
    )


def _has_stable_knowledge_anchor(message: str) -> bool:
    normalized = str(message or "").strip()
    lowered = normalized.lower()
    return bool(
        "knowledge base" in lowered
        or "based on the knowledge" in lowered
        or "from the knowledge" in lowered
        or any(token in normalized for token in ("知识库", "根据知识库", "基于知识库", "从知识库"))
    )


def deterministic_route(
    *,
    message: str,
    strategy,
    tool_names: tuple[str, ...],
    is_knowledge_query: bool,
    prefer_tool_agent: bool,
) -> RoutingDecision | None:
    normalized = str(message or "").strip()
    lowered = normalized.lower()
    allowed_tool_names = set(tool_names)
    explicit_doc_seek = _has_explicit_doc_seek(normalized)

    if strategy.force_direct_answer or (not strategy.allow_tools and not strategy.allow_retrieval):
        return RoutingDecision(
            intent="direct_answer",
            needs_tools=False,
            needs_retrieval=False,
            allowed_tools=(),
            confidence=1.0,
            reason_short="hard constraints force direct answer",
            source="rules",
        )

    if strategy.allowed_tools:
        allowed_tools = _normalize_allowed_tools(sorted(strategy.allowed_tools), allowed_tool_names)
        if allowed_tools == ("python_repl",):
            intent = "computation_or_transformation"
            subtype = "code_execution_request"
        elif allowed_tools == ("fetch_url",):
            intent = "web_lookup"
            subtype = ""
        elif allowed_tools == ("mcp_web_fetch_url",):
            intent = "web_lookup"
            subtype = ""
        elif allowed_tools == ("read_file",):
            intent = "workspace_file_ops"
            subtype = "read_existing_file"
        else:
            intent = "workspace_file_ops"
            subtype = "search_workspace_file"
        return RoutingDecision(
            intent=intent,
            needs_tools=bool(allowed_tools),
            needs_retrieval=False,
            allowed_tools=allowed_tools,
            confidence=1.0,
            reason_short="hard constraints select tools",
            source="rules",
            subtype=subtype,
        )

    if strategy.allow_tools and any(pattern.search(normalized) for pattern in MCP_FILESYSTEM_PATTERNS):
        subtype = "search_workspace_file"
        if any(pattern.search(normalized) for pattern in READ_FILE_PATTERNS):
            subtype = "read_existing_file"
        elif any(pattern.search(normalized) for pattern in SEARCH_FILE_PATTERNS):
            subtype = "search_workspace_file"
        allowed_tools = _intent_tools("workspace_file_ops", subtype, normalized, allowed_tool_names)
        if allowed_tools:
            return RoutingDecision(
                intent="workspace_file_ops",
                needs_tools=True,
                needs_retrieval=False,
                allowed_tools=allowed_tools,
                confidence=0.95,
                reason_short="explicit filesystem mcp request",
                source="rules",
                subtype=subtype,
            )

    if strategy.allow_tools and any(pattern.search(normalized) for pattern in MCP_WEB_PATTERNS):
        allowed_tools = _intent_tools("web_lookup", "", normalized, allowed_tool_names)
        if allowed_tools:
            return RoutingDecision(
                intent="web_lookup",
                needs_tools=True,
                needs_retrieval=False,
                allowed_tools=allowed_tools,
                confidence=0.95,
                reason_short="explicit web mcp request",
                source="rules",
            )

    if strategy.allow_tools and _is_workspace_request(normalized) and _has_explicit_workspace_anchor(normalized):
        subtype = _workspace_subtype(normalized)
        if (
            subtype != "modify_or_run_in_workspace"
            and any(pattern.search(normalized) for pattern in COMPUTE_PATTERNS)
            and any(pattern.search(normalized) for pattern in FILE_BACKED_PATTERNS)
        ):
            subtype = "file_backed_calculation"
            intent = "computation_or_transformation"
        else:
            intent = "workspace_file_ops"
        allowed_tools = _intent_tools(intent, subtype, normalized, allowed_tool_names)
        return RoutingDecision(
            intent=intent,
            needs_tools=bool(allowed_tools),
            needs_retrieval=False,
            allowed_tools=allowed_tools,
            confidence=0.9,
            reason_short="clear workspace operation",
            source="rules",
            subtype=subtype,
        )

    if strategy.allow_tools and any(pattern.search(normalized) for pattern in WEB_LOOKUP_PATTERNS):
        allowed_tools = _intent_tools("web_lookup", "", normalized, allowed_tool_names)
        return RoutingDecision(
            intent="web_lookup",
            needs_tools=bool(allowed_tools),
            needs_retrieval=False,
            allowed_tools=allowed_tools,
            confidence=0.9,
            reason_short="clear web request",
            source="rules",
        )

    if any(pattern.search(normalized) for pattern in DIRECT_PATTERNS) and not any(pattern.search(normalized) for pattern in COMPUTE_PATTERNS):
        direct_subtype = _compute_subtype(normalized) if any(
            term in lowered for term in ("rewrite", "summarize", "translate", "改写", "总结", "翻译")
        ) else ""
        if direct_subtype == "pure_text_transformation":
            return RoutingDecision(
                intent="direct_answer",
                needs_tools=False,
                needs_retrieval=False,
                allowed_tools=(),
                confidence=0.9,
                reason_short="clear direct-answer request",
                source="rules",
                subtype=direct_subtype,
            )

    if any(pattern.search(normalized) for pattern in COMPUTE_PATTERNS):
        subtype = _compute_subtype(normalized)
        if subtype == "pure_calculation":
            if not any(pattern.search(normalized) for pattern in FILE_BACKED_PATTERNS):
                return RoutingDecision(
                    intent="direct_answer",
                    needs_tools=False,
                    needs_retrieval=False,
                    allowed_tools=(),
                    confidence=0.82,
                    reason_short="simple calculation can be direct",
                    source="rules",
                    subtype=subtype,
                )
        if subtype == "pure_text_transformation":
            return RoutingDecision(
                intent="direct_answer",
                needs_tools=False,
                needs_retrieval=False,
                allowed_tools=(),
                confidence=0.88,
                reason_short="text transformation can be direct",
                source="rules",
                subtype=subtype,
            )
        allowed_tools = _intent_tools("computation_or_transformation", subtype, normalized, allowed_tool_names)
        return RoutingDecision(
            intent="computation_or_transformation",
            needs_tools=bool(allowed_tools),
            needs_retrieval=False,
            allowed_tools=allowed_tools,
            confidence=0.84,
            reason_short="clear computation request",
            source="rules",
            subtype=subtype,
        )

    if (
        is_knowledge_query
        and _has_stable_knowledge_anchor(normalized)
        and strategy.allow_knowledge
        and strategy.allow_retrieval
        and not prefer_tool_agent
    ):
        return RoutingDecision(
            intent="knowledge_qa",
            needs_tools=False,
            needs_retrieval=True,
            allowed_tools=(),
            confidence=0.95,
            reason_short="explicit knowledge-base request",
            source="rules",
        )

    return None


class LightweightLLMRouter:
    def __init__(self) -> None:
        self._small_model = None
        self._large_model = None

    def _create_model(self, *, model_name: str, api_key: str, base_url: str, thinking_type: str | None):
        from langchain_openai import ChatOpenAI  # pylint: disable=import-outside-toplevel

        temperature = 1 if str(model_name or "").startswith("kimi-") else 0.1
        kwargs: dict[str, Any] = {
            "model": model_name,
            "api_key": api_key,
            "base_url": base_url,
            "temperature": temperature,
            "max_tokens": 120,
        }
        if model_name == "kimi-k2.5" and thinking_type:
            kwargs["extra_body"] = {"thinking": {"type": thinking_type}}
            if thinking_type == "disabled":
                kwargs["temperature"] = None
        return ChatOpenAI(**kwargs)

    def _build_small_model(self):
        settings = get_settings()
        if self._small_model is not None:
            return self._small_model, settings.router_model
        if not settings.router_api_key:
            raise RuntimeError("Missing router API key")
        try:
            self._small_model = self._create_model(
                model_name=settings.router_model,
                api_key=settings.router_api_key,
                base_url=settings.router_base_url,
                thinking_type=None,
            )
            return self._small_model, settings.router_model
        except Exception:
            self._small_model = self._build_large_model()
            return self._small_model, settings.llm_model

    def _build_large_model(self):
        if self._large_model is not None:
            return self._large_model
        settings = get_settings()
        if not settings.llm_api_key:
            raise RuntimeError("Missing fallback router API key")
        self._large_model = self._create_model(
            model_name=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            thinking_type=settings.llm_thinking_type,
        )
        return self._large_model

    def _router_prompt(
        self,
        *,
        message: str,
        history_excerpt: str,
        hard_constraints: str,
        tool_names: tuple[str, ...],
        mode: str,
    ) -> list[dict[str, str]]:
        system = (
            "You are a request routing model for a grounded harness runtime. "
            "Return JSON only. "
            "Use intent from: direct_answer, knowledge_qa, workspace_file_ops, computation_or_transformation, web_lookup. "
            "Use subtype when relevant from: read_existing_file, search_workspace_file, modify_or_run_in_workspace, pure_calculation, file_backed_calculation, pure_text_transformation, code_execution_request. "
            f"Allowed tools must be from: {', '.join(tool_names)}."
        )
        if mode == "resolver":
            system += " Resolve ambiguity carefully and keep output minimal."
        system += "\n" + ROUTER_CAPABILITY_GUIDE
        user = (
            f"latest_message: {message}\n"
            f"recent_context: {history_excerpt}\n"
            f"hard_constraints: {hard_constraints}\n"
            "Return JSON with keys: intent, subtype, needs_tools, needs_retrieval, allowed_tools, confidence, reason_short.\n"
            "If unsure between knowledge and workspace, keep confidence lower and choose the safer likely route.\n"
            "Keep reason_short very short and do not include chain-of-thought."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _coerce_non_json_response(self, raw: str) -> dict[str, Any]:
        lowered = str(raw or "").strip().lower()
        if not lowered:
            raise ValueError("router returned empty content")
        if any(term in lowered for term in ("knowledge", "document", "source path", "knowledge base", "report", "faq")):
            intent = "knowledge_qa"
        elif any(term in lowered for term in ("python_repl", "calculate", "count")):
            intent = "computation_or_transformation"
        elif any(term in lowered for term in ("fetch_url", "website", "web")):
            intent = "web_lookup"
        elif any(term in lowered for term in ("read_file", "terminal", "workspace", "backend/", "repo", "file")):
            intent = "workspace_file_ops"
        else:
            intent = "direct_answer"
        allowed_tools = [tool for tool in ("python_repl", "fetch_url", "read_file", "terminal") if tool in lowered]
        return {
            "intent": intent,
            "subtype": "",
            "needs_tools": intent in {"workspace_file_ops", "computation_or_transformation", "web_lookup"},
            "needs_retrieval": intent == "knowledge_qa",
            "allowed_tools": allowed_tools,
            "confidence": 0.55,
            "reason_short": "parsed non-json router output",
        }

    def _normalize_subtype(self, intent: str, subtype: str, message: str) -> str:
        subtype = str(subtype or "").strip()
        if intent == "workspace_file_ops":
            return subtype if subtype in WORKSPACE_SUBTYPES else _workspace_subtype(message)
        if intent == "computation_or_transformation":
            return subtype if subtype in COMPUTE_SUBTYPES else _compute_subtype(message)
        return ""

    def _parse_response(self, raw: str, *, message: str, tool_names: tuple[str, ...], source: str, model_name: str) -> RoutingDecision:
        match = re.search(r"\{.*\}", str(raw or ""), re.DOTALL)
        payload = json.loads(match.group(0)) if match else self._coerce_non_json_response(raw)
        allowed_tool_names = set(tool_names)
        intent = str(payload.get("intent", "") or "").strip()
        if intent not in ROUTER_INTENTS:
            raise ValueError(f"invalid router intent: {intent}")
        subtype = self._normalize_subtype(intent, str(payload.get("subtype", "") or ""), message)
        router_tools = payload.get("allowed_tools", [])
        if not isinstance(router_tools, list):
            router_tools = []
        normalized_router_tools = set(_normalize_allowed_tools(router_tools, allowed_tool_names))
        intent_tools = set(_intent_tools(intent, subtype, message, allowed_tool_names))
        merged_tools = tuple(sorted(normalized_router_tools & intent_tools if intent_tools else normalized_router_tools))
        if intent != "direct_answer" and not merged_tools and intent in {"workspace_file_ops", "computation_or_transformation", "web_lookup"}:
            merged_tools = _intent_tools(intent, subtype, message, allowed_tool_names)
        try:
            confidence_value = max(0.0, min(1.0, float(payload.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence_value = 0.0
        reason_short = str(payload.get("reason_short", "") or "").strip()[:120]
        needs_tools = bool(payload.get("needs_tools", False) or merged_tools)
        needs_retrieval = bool(payload.get("needs_retrieval", False) or intent == "knowledge_qa")
        if intent == "direct_answer":
            needs_tools = False
            needs_retrieval = False
            merged_tools = ()
            subtype = ""
        if intent == "knowledge_qa":
            needs_tools = False
            needs_retrieval = True
            merged_tools = ()
            subtype = ""
        return RoutingDecision(
            intent=intent,
            needs_tools=needs_tools,
            needs_retrieval=needs_retrieval,
            allowed_tools=tuple(merged_tools),
            confidence=confidence_value,
            reason_short=reason_short or "llm router",
            source=source,
            model_name=model_name,
            subtype=subtype,
        )

    def _ambiguity_flags(self, message: str, history: list[dict[str, Any]], decision: RoutingDecision) -> tuple[str, ...]:
        normalized = str(message or "").strip()
        flags: list[str] = []
        for label, pattern in AMBIGUOUS_PATTERNS:
            if pattern.search(normalized):
                flags.append(label)
        if history and len(_history_excerpt(history)) > 0:
            flags.append("context_dependent")
        if decision.confidence < 0.67:
            flags.append("low_confidence")
        if len(decision.allowed_tools) > 1:
            flags.append("tool_conflict")
        return tuple(dict.fromkeys(flags))

    def _should_escalate(self, decision: RoutingDecision, ambiguity_flags: tuple[str, ...], used_large_as_small: bool) -> bool:
        if used_large_as_small:
            return False
        return bool(
            "low_confidence" in ambiguity_flags
            or "fuzzy_document_seeking" in ambiguity_flags
            or "mixed_intent" in ambiguity_flags
            or "ambiguous_language" in ambiguity_flags
            or "tool_conflict" in ambiguity_flags
        )

    async def _invoke_router(
        self,
        *,
        model,
        model_name: str,
        source: str,
        message: str,
        history: list[dict[str, Any]],
        strategy,
        tool_names: tuple[str, ...],
        mode: str,
    ) -> RoutingDecision:
        model_messages = self._router_prompt(
            message=message,
            history_excerpt=_history_excerpt(history),
            hard_constraints=summarize_hard_constraints(strategy),
            tool_names=tool_names,
            mode=mode,
        )
        prompt_tokens = count_tokens("\n\n".join(f"{item['role']}: {item['content']}" for item in model_messages))
        response = await model.ainvoke(model_messages)
        raw = str(getattr(response, "content", "") or "")
        decision = self._parse_response(raw, message=message, tool_names=tool_names, source=source, model_name=model_name)
        return RoutingDecision(
            intent=decision.intent,
            needs_tools=decision.needs_tools,
            needs_retrieval=decision.needs_retrieval,
            allowed_tools=decision.allowed_tools,
            confidence=decision.confidence,
            reason_short=decision.reason_short,
            source=source,
            prompt_tokens=prompt_tokens,
            output_tokens=count_tokens(raw),
            model_name=model_name,
            subtype=decision.subtype,
        )

    async def route(
        self,
        *,
        message: str,
        history: list[dict[str, Any]],
        strategy,
        tool_names: tuple[str, ...],
    ) -> RoutingDecision:
        settings = get_settings()
        try:
            primary_decision = await self._invoke_router(
                model=self._build_large_model(),
                model_name=settings.llm_model,
                source="llm_router",
                message=message,
                history=history,
                strategy=strategy,
                tool_names=tool_names,
                mode="resolver",
            )
        except Exception:
            small_model, small_model_name = self._build_small_model()
            primary_decision = await self._invoke_router(
                model=small_model,
                model_name=small_model_name,
                source="llm_router_fallback",
                message=message,
                history=history,
                strategy=strategy,
                tool_names=tool_names,
                mode="resolver",
            )

        ambiguity_flags = self._ambiguity_flags(message, history, primary_decision)
        return RoutingDecision(
            intent=primary_decision.intent,
            needs_tools=primary_decision.needs_tools,
            needs_retrieval=primary_decision.needs_retrieval,
            allowed_tools=primary_decision.allowed_tools,
            confidence=primary_decision.confidence,
            reason_short=primary_decision.reason_short,
            source=primary_decision.source,
            prompt_tokens=primary_decision.prompt_tokens,
            output_tokens=primary_decision.output_tokens,
            ambiguity_flags=ambiguity_flags,
            escalated=False,
            model_name=primary_decision.model_name,
            subtype=primary_decision.subtype,
        )
