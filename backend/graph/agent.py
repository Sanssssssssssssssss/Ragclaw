from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from config import get_settings, runtime_config
from graph.execution_strategy import ExecutionStrategy, parse_execution_strategy
from graph.lightweight_router import RoutingDecision, LightweightLLMRouter, deterministic_route
from graph.memory_indexer import memory_indexer
from graph.prompt_builder import build_knowledge_system_prompt, build_system_prompt
from graph.skill_gate import SkillDecision, SkillGate, skill_instruction
from graph.session_manager import SessionManager
from knowledge_retrieval import knowledge_orchestrator
from token_utils import count_tokens
from tools import get_all_tools

KNOWLEDGE_SKILL_PATTERNS = (
    re.compile(r"知识库"),
    re.compile(r"\bknowledge\b", re.IGNORECASE),
    re.compile(r"根据.+?(知识库|文档|资料)"),
    re.compile(r"(查|检索).+?(文档|资料|报告|白皮书)"),
    re.compile(r"\.(pdf|xlsx|xls|json)\b", re.IGNORECASE),
)
WORKSPACE_OPERATION_PATTERNS = (
    re.compile(r"(?:读取|打开|列出|查看|统计|提取|分析|显示).{0,40}(?:knowledge/|workspace/|memory/|storage/)", re.IGNORECASE),
    re.compile(r"(?:read|open|list|count|extract|analyze|show).{0,60}(?:knowledge/|workspace/|memory/|storage/)", re.IGNORECASE),
)

ACTION_ONLY_PATTERNS = (
    re.compile(r"^(?:我来|让我|我会|我将|下面我)(?:使用|调用)?.{0,30}(?:tool|terminal|python_repl|read_file|fetch_url)", re.IGNORECASE),
    re.compile(r"^(?:i'll|i will|let me)\s+(?:use|call).{0,30}(?:tool|terminal|python_repl|read_file|fetch_url)", re.IGNORECASE),
)
STABLE_KNOWLEDGE_QUERY_SUBSTRINGS = (
    "知识库",
    "根据知识库",
    "基于知识库",
    "从知识库",
    "knowledge base",
)
STABLE_KNOWLEDGE_QUERY_PATTERNS = (
    re.compile(r"\bknowledge\b", re.IGNORECASE),
    re.compile(r"\b(retrieval|rag)\b", re.IGNORECASE),
    re.compile(r"\.(md|json|txt|pdf|xlsx|xls)\b", re.IGNORECASE),
    re.compile(r"(哪份|哪个|哪张|那个).{0,30}(文档|文件|报告|财报|路径|来源)"),
    re.compile(r"(给出|返回).{0,12}(路径|来源)"),
    re.compile(r"\b(which|what)\b.{0,24}\b(file|document|report|path|source)\b", re.IGNORECASE),
)
STABLE_WORKSPACE_OPERATION_PATTERNS = (
    re.compile(r"(?:读取|打开|列出|查看|统计|提取|分析|显示).{0,40}(?:knowledge/|workspace/|memory/|storage/)", re.IGNORECASE),
    re.compile(r"(?:read|open|list|count|extract|analyze|show).{0,60}(?:knowledge/|workspace/|memory/|storage/)", re.IGNORECASE),
)


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content or "")


def _serialize_model_messages(messages: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for item in messages:
        role = str(item.get("role", "")).strip() or "unknown"
        content = str(item.get("content", "") or "")
        parts.append(f"{role}: {content}")
    return "\n\n".join(parts)


def _incremental_text(previous: str, current: str) -> str:
    """Return only the newly appended suffix when a stream emits cumulative text snapshots."""

    prev = str(previous or "")
    curr = str(current or "")
    if not curr:
        return ""
    if not prev:
        return curr
    if curr == prev:
        return ""
    if curr.startswith(prev):
        return curr[len(prev) :]
    if prev.startswith(curr):
        return ""

    max_overlap = min(len(prev), len(curr))
    for overlap in range(max_overlap, 0, -1):
        if prev.endswith(curr[:overlap]):
            return curr[overlap:]
    return curr


class AgentManager:
    def __init__(self) -> None:
        self.base_dir: Path | None = None
        self.session_manager: SessionManager | None = None
        self.tools = []
        self._lightweight_router = LightweightLLMRouter()
        self._skill_gate = SkillGate()

    def initialize(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.session_manager = SessionManager(base_dir)
        self.tools = get_all_tools(base_dir)
        knowledge_orchestrator.configure(base_dir, self._build_chat_model)

    def _build_openai_chat_model_kwargs(self, settings) -> dict[str, Any]:
        """Return provider kwargs for ChatOpenAI using the current settings object."""
        kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "api_key": settings.llm_api_key,
            "base_url": settings.llm_base_url,
            "temperature": 1,
        }

        if settings.llm_model == "kimi-k2.5" and settings.llm_thinking_type:
            kwargs["extra_body"] = {"thinking": {"type": settings.llm_thinking_type}}
            if settings.llm_thinking_type == "disabled":
                kwargs["temperature"] = None
            else:
                kwargs["temperature"] = 1

        return kwargs

    def _build_chat_model(self):
        settings = get_settings()

        if settings.llm_provider == "deepseek":
            try:
                from langchain_deepseek import ChatDeepSeek
            except ImportError as exc:  # pragma: no cover - optional dependency at runtime
                raise RuntimeError("langchain-deepseek is not installed") from exc
            if ChatDeepSeek is None:
                raise RuntimeError("langchain-deepseek is not installed")
            if not settings.llm_api_key:
                raise RuntimeError("Missing API key for provider deepseek")
            return ChatDeepSeek(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                temperature=1,
            )

        if not settings.llm_api_key:
            raise RuntimeError(f"Missing API key for provider {settings.llm_provider}")

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(**self._build_openai_chat_model_kwargs(settings))

    def _build_agent(
        self,
        extra_instructions: list[str] | None = None,
        tools_override: list[Any] | None = None,
    ):
        if self.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")

        from langchain.agents import create_agent

        system_prompt = build_system_prompt(self.base_dir, runtime_config.get_rag_mode())
        if extra_instructions:
            system_prompt = f"{system_prompt}\n\n" + "\n\n".join(extra_instructions)
        return create_agent(
            model=self._build_chat_model(),
            tools=self.tools if tools_override is None else tools_override,
            system_prompt=system_prompt,
        )

    def _resolve_tools_for_strategy(self, strategy: ExecutionStrategy) -> list[Any]:
        """Return the tool list allowed by one execution strategy."""

        if not strategy.allow_tools:
            return []

        allowed_tools = list(self.tools)
        if strategy.allowed_tools:
            allowed_names = set(strategy.allowed_tools)
            allowed_tools = [tool for tool in allowed_tools if getattr(tool, "name", "") in allowed_names]

        if strategy.blocked_tools:
            blocked_names = set(strategy.blocked_tools)
            allowed_tools = [tool for tool in allowed_tools if getattr(tool, "name", "") not in blocked_names]

        return allowed_tools

    def _tool_name_tuple(self) -> tuple[str, ...]:
        return tuple(sorted(str(getattr(tool, "name", "") or "").strip() for tool in self.tools if getattr(tool, "name", "")))

    def _fallback_routing_decision(self, message: str, strategy: ExecutionStrategy) -> RoutingDecision:
        if self._is_knowledge_query(message) and strategy.allow_knowledge and strategy.allow_retrieval:
            return RoutingDecision(
                intent="knowledge_qa",
                needs_tools=False,
                needs_retrieval=True,
                allowed_tools=(),
                confidence=0.5,
                reason_short="fallback knowledge heuristic",
                source="fallback",
            )
        if strategy.allow_tools and self._is_workspace_request(message):
            return RoutingDecision(
                intent="workspace_file_ops",
                needs_tools=True,
                needs_retrieval=False,
                allowed_tools=("read_file", "terminal"),
                confidence=0.45,
                reason_short="fallback workspace heuristic",
                source="fallback",
            )
        return RoutingDecision(
            intent="direct_answer",
            needs_tools=False,
            needs_retrieval=False,
            allowed_tools=(),
            confidence=0.4,
            reason_short="fallback direct answer",
            source="fallback",
        )

    def _apply_routing_constraints(self, decision: RoutingDecision, strategy: ExecutionStrategy) -> RoutingDecision:
        allowed_tools = list(decision.allowed_tools)
        if strategy.allowed_tools:
            allowed_tools = [tool for tool in allowed_tools if tool in strategy.allowed_tools]
        if strategy.blocked_tools:
            allowed_tools = [tool for tool in allowed_tools if tool not in strategy.blocked_tools]
        if not strategy.allow_tools:
            allowed_tools = []

        intent = decision.intent
        needs_retrieval = decision.needs_retrieval and strategy.allow_knowledge and strategy.allow_retrieval
        needs_tools = bool(allowed_tools) and strategy.allow_tools

        if intent == "knowledge_qa" and not needs_retrieval:
            intent = "direct_answer" if not needs_tools else "workspace_file_ops"
        if intent == "direct_answer":
            allowed_tools = []
            needs_tools = False
            needs_retrieval = False
        elif intent == "knowledge_qa":
            allowed_tools = []
            needs_tools = False
            needs_retrieval = True

        if strategy.force_direct_answer:
            intent = "direct_answer"
            allowed_tools = []
            needs_tools = False
            needs_retrieval = False

        return RoutingDecision(
            intent=intent,
            needs_tools=needs_tools,
            needs_retrieval=needs_retrieval,
            allowed_tools=tuple(allowed_tools),
            confidence=decision.confidence,
            reason_short=decision.reason_short,
            source=decision.source,
            prompt_tokens=decision.prompt_tokens,
            output_tokens=decision.output_tokens,
            ambiguity_flags=tuple(getattr(decision, "ambiguity_flags", ()) or ()),
            escalated=bool(getattr(decision, "escalated", False)),
            model_name=str(getattr(decision, "model_name", "") or ""),
            subtype=str(getattr(decision, "subtype", "") or ""),
        )

    async def resolve_routing(self, message: str, history: list[dict[str, Any]]) -> tuple[ExecutionStrategy, RoutingDecision]:
        strategy = parse_execution_strategy(message)
        tool_names = self._tool_name_tuple()
        decision = deterministic_route(
            message=message,
            strategy=strategy,
            tool_names=tool_names,
            is_knowledge_query=self._is_knowledge_query(message),
            prefer_tool_agent=self._should_prefer_tool_agent(message, strategy),
        )
        if decision is None:
            try:
                decision = await self._lightweight_router.route(
                    message=message,
                    history=history,
                    strategy=strategy,
                    tool_names=tool_names,
                )
            except Exception:
                decision = self._fallback_routing_decision(message, strategy)
        return strategy, self._apply_routing_constraints(decision, strategy)

    def decide_skill(
        self,
        message: str,
        history: list[dict[str, Any]],
        strategy: ExecutionStrategy,
        routing_decision: RoutingDecision,
    ) -> SkillDecision:
        return self._skill_gate.decide(
            message=message,
            history=history,
            strategy=strategy,
            routing_decision=routing_decision,
        )

    def _is_knowledge_query(self, message: str) -> bool:
        normalized = str(message or "").replace("\\", "/").strip()
        lowered = normalized.lower()
        if any(token in lowered for token in ("repo", "workspace/", "backend/", "memory/")) and "知识库" not in normalized and "knowledge base" not in lowered:
            return False
        stable_substrings = (
            "知识库",
            "根据知识库",
            "基于知识库",
            "从知识库",
            "knowledge base",
            "knowledge/",
        )
        if any(token in normalized for token in stable_substrings):
            return True
        if any(
            pattern.search(normalized)
            for pattern in (
                re.compile(r"\bknowledge\b", re.IGNORECASE),
                re.compile(r"\.(md|json|txt|pdf|xlsx|xls)\b", re.IGNORECASE),
                re.compile(r"(哪份|哪个|哪张|那个|那份).{0,30}(文档|文件|报告|财报|路径|来源)"),
                re.compile(r"(给出|返回).{0,12}(路径|来源)"),
                re.compile(r"\b(which|what)\b.{0,24}\b(file|document|report|path|source)\b", re.IGNORECASE),
            )
        ):
            return True
        return lowered.startswith("based on the knowledge") or lowered.startswith("from the knowledge")

    def _is_workspace_request(self, message: str) -> bool:
        normalized = str(message or "").replace("\\", "/").strip()
        return any(
            pattern.search(normalized)
            for pattern in (
                re.compile(r"(?:读取|打开|列出|查看|统计|提取|分析|显示|解析|转换|计算).{0,40}(?:knowledge/|workspace/|memory/|storage/|backend/)", re.IGNORECASE),
                re.compile(r"(?:read|open|list|count|extract|analyze|show|parse|convert|calculate|compute).{0,60}(?:knowledge/|workspace/|memory/|storage/|backend/)", re.IGNORECASE),
            )
        )

    def _should_prefer_tool_agent(self, message: str, strategy: ExecutionStrategy) -> bool:
        """Return whether the request should bypass knowledge routing and go straight to tools."""

        if strategy.require_tool_use or strategy.allowed_tools:
            return True
        return self._is_workspace_request(message)

    def _build_messages(self, history: list[dict[str, Any]]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for item in history:
            role = item.get("role")
            if role not in {"user", "assistant"}:
                continue
            messages.append({"role": role, "content": str(item.get("content", ""))})
        return messages

    def _format_retrieval_context(self, results: list[dict[str, Any]]) -> str:
        lines = ["[RAG retrieved memory context]"]
        for idx, item in enumerate(results, start=1):
            text = str(item.get("text", "")).strip()
            source = str(item.get("source", "memory/MEMORY.md"))
            lines.append(f"{idx}. Source: {source}\n{text}")
        return "\n\n".join(lines)

    def _format_memory_retrieval_step(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "kind": "memory",
            "stage": "memory",
            "title": f"Memory 检索到 {len(results)} 条片段",
            "message": "已将 Memory 召回结果注入当前请求上下文。",
            "results": [
                {
                    "source_path": str(item.get("source", "memory/MEMORY.md")),
                    "source_type": "memory",
                    "locator": "memory",
                    "snippet": str(item.get("text", "")).strip(),
                    "channel": "memory",
                    "score": float(item.get("score", 0.0) or 0.0),
                    "parent_id": None,
                }
                for item in results
            ],
        }

    def _format_knowledge_context(self, retrieval_result) -> str:
        lines = ["[Knowledge retrieval evidence]"]
        if not retrieval_result.evidences:
            lines.append("No direct evidence was found.")
            return "\n".join(lines)

        for index, evidence in enumerate(retrieval_result.evidences, start=1):
            header_parts = [f"{index}. {evidence.source_path}"]
            locator = str(getattr(evidence, "locator", "") or "").strip()
            if locator:
                header_parts.append(f"loc: {locator}")
            supporting_children = getattr(evidence, "supporting_children", None)
            if supporting_children:
                header_parts.append(f"merged: {supporting_children}")
            lines.append(f"{' | '.join(header_parts)}\n{evidence.snippet}")
        return "\n\n".join(lines)

    def _knowledge_question_type(self, retrieval_result) -> str:
        return str(getattr(retrieval_result, "question_type", "") or "direct_fact").strip().lower()

    def _evidence_missing_text(self) -> str:
        return "\u5f53\u524d\u8bc1\u636e\u672a\u663e\u793a"

    def _knowledge_entities(self, message: str, retrieval_result) -> list[str]:
        compare_segment = ""
        compare_match = re.search(r"\u5bf9\u6bd4(.+?)(?:20\d{2}|Q\d|\u7684|\u51c0\u5229\u6da6|\u8425\u4e1a\u6536\u5165)", str(message or ""))
        if compare_match:
            compare_segment = compare_match.group(1)
        if compare_segment:
            parsed = [
                part.strip()
                for part in re.split(r"[\u4e0e\u548c\u53ca\u3001/]", compare_segment)
                if len(part.strip()) >= 2
            ]
            if len(parsed) >= 2:
                return self._dedupe_preserve_order(parsed)[:2]

        entities = [
            str(item).strip()
            for item in getattr(retrieval_result, "entity_hints", []) or []
            if len(str(item).strip()) >= 2
        ]
        if len(entities) >= 2:
            return self._dedupe_preserve_order(entities)[:2]

        derived: list[str] = []
        for evidence in getattr(retrieval_result, "evidences", []) or []:
            source_path = str(getattr(evidence, "source_path", "") or "").replace("\\", "/")
            stem = Path(source_path).stem
            stem = re.sub(r"\s*20\d{2}\s*Q\d.*$", "", stem).strip()
            stem = re.sub(r"_20\d{2}_Q\d.*$", "", stem).strip()
            if stem:
                derived.append(stem)
        return self._dedupe_preserve_order(entities + derived)[:2]

    def _entity_aliases(self, entity: str) -> list[str]:
        raw = str(entity or "").strip()
        aliases = [raw]
        for suffix in ("\u80a1\u4efd\u6709\u9650\u516c\u53f8", "\u6709\u9650\u516c\u53f8", "\u96c6\u56e2", "\u516c\u53f8"):
            if raw.endswith(suffix):
                aliases.append(raw[: -len(suffix)].strip())
        return self._dedupe_preserve_order([alias for alias in aliases if len(alias) >= 2])

    def _snippet_lines(self, text: str) -> list[str]:
        parts = re.split(r"[\n\r]+|(?<=\|)\s+|(?<=\u3002)\s*", str(text or ""))
        return [part.strip() for part in parts if part and part.strip()]

    def _evidence_matches_entity(self, evidence: Any, entity: str) -> bool:
        blob = " ".join(
            [
                str(getattr(evidence, "source_path", "") or ""),
                str(getattr(evidence, "locator", "") or ""),
                str(getattr(evidence, "snippet", "") or ""),
            ]
        ).lower()
        return any(alias.lower() in blob for alias in self._entity_aliases(entity))

    def _requested_period_scope(self, message: str) -> str:
        lowered = str(message or "").lower()
        if "\u5e74\u521d\u81f3\u62a5\u544a\u671f\u672b" in message or "\u524d\u4e09\u5b63\u5ea6" in message:
            return "ytd"
        if any(term in message for term in ("\u672c\u62a5\u544a\u671f", "\u5355\u5b63\u5ea6", "\u5f53\u5b63", "\u6b63\u8d1f")):
            return "report"
        if "q3" in lowered and any(term in message for term in ("\u53d8\u5316", "\u5bf9\u6bd4", "\u6bd4\u8f83")):
            return "both"
        return "report"

    def _period_markers_for_scope(self, scope: str) -> tuple[list[str], list[str]]:
        report_markers = ["\u672c\u62a5\u544a\u671f", "\u672c\u671f", "\u5355\u5b63\u5ea6", "q3", "\u7b2c\u4e09\u5b63\u5ea6"]
        ytd_markers = ["\u5e74\u521d\u81f3\u62a5\u544a\u671f\u672b", "\u524d\u4e09\u5b63\u5ea6", "\u7d2f\u8ba1", "1-9\u6708"]
        if scope == "ytd":
            return ytd_markers, report_markers
        return report_markers, ytd_markers

    def _extract_amounts(self, text: str) -> list[str]:
        pattern = re.compile(
            r"-?\d[\d,]*(?:\.\d+)?\s*(?:\u4ebf\u5143|\u4e07\u5143|\u5143|\u5343\u5143|\u4e07\u4ebf)?",
            re.IGNORECASE,
        )
        matches: list[str] = []
        for match in pattern.finditer(str(text or "")):
            token = match.group(0).strip()
            digits = re.sub(r"\D+", "", token)
            has_unit = any(unit in token for unit in ("\u4ebf\u5143", "\u4e07\u5143", "\u5143", "\u5343\u5143", "\u4e07\u4ebf"))
            has_grouping = "," in token
            if has_unit or has_grouping or len(digits) >= 7:
                matches.append(token)
        return self._dedupe_preserve_order(matches)

    def _extract_percentages(self, text: str) -> list[str]:
        return self._dedupe_preserve_order(
            [match.group(0).strip() for match in re.finditer(r"-?\d[\d,]*(?:\.\d+)?\s*%", str(text or ""))]
        )

    def _collect_metric_candidates(self, entity: str, metric_terms: list[str], message: str, retrieval_result) -> list[dict[str, str | int]]:
        candidates: list[dict[str, str | int]] = []
        scope = self._requested_period_scope(message)
        preferred_markers, secondary_markers = self._period_markers_for_scope(scope)
        noise_terms = ["\u666e\u901a\u80a1\u80a1\u4e1c\u603b\u6570", "\u8d28\u62bc", "\u80a1\u4efd\u6570\u91cf", "\u6301\u80a1", "\u524d\u5341\u540d\u80a1\u4e1c"]

        for evidence in getattr(retrieval_result, "evidences", []) or []:
            if not self._evidence_matches_entity(evidence, entity):
                continue
            source_path = str(getattr(evidence, "source_path", "") or "").strip()
            locator = str(getattr(evidence, "locator", "") or "").strip()
            snippet = str(getattr(evidence, "snippet", "") or "").strip()
            lines = self._snippet_lines(snippet) or [snippet]
            for line in lines:
                lowered = line.lower()
                if any(term in line for term in noise_terms):
                    continue
                has_metric_term = any(term.lower() in lowered for term in metric_terms)
                if not has_metric_term:
                    continue
                score = 0
                score += 5
                if any(marker.lower() in lowered for marker in preferred_markers):
                    score += 3
                if any(marker.lower() in lowered for marker in secondary_markers):
                    score += 1
                if self._extract_amounts(line):
                    score += 2
                if self._extract_percentages(line):
                    score += 1
                if score <= 0:
                    continue
                candidates.append(
                    {
                        "source_path": source_path,
                        "locator": locator,
                        "line": line,
                        "score": score,
                    }
                )
        return sorted(candidates, key=lambda item: int(item["score"]), reverse=True)

    def _parse_period_values_from_line(self, line: str, scope: str) -> dict[str, str]:
        amounts = self._extract_amounts(line)
        percentages = self._extract_percentages(line)
        lowered = line.lower()
        report_markers, ytd_markers = self._period_markers_for_scope("both")
        has_report = any(marker.lower() in lowered for marker in report_markers)
        has_ytd = any(marker.lower() in lowered for marker in ytd_markers)
        data = {
            "report_value": self._evidence_missing_text(),
            "report_yoy": self._evidence_missing_text(),
            "ytd_value": self._evidence_missing_text(),
            "ytd_yoy": self._evidence_missing_text(),
        }

        if scope == "ytd":
            if amounts:
                data["ytd_value"] = amounts[-1]
            if percentages:
                data["ytd_yoy"] = percentages[-1]
            return data

        if len(amounts) >= 2:
            data["report_value"] = amounts[0]
            data["ytd_value"] = amounts[1]
        elif amounts:
            if has_ytd and not has_report:
                data["ytd_value"] = amounts[0]
            else:
                data["report_value"] = amounts[0]

        if len(percentages) >= 2:
            data["report_yoy"] = percentages[0]
            data["ytd_yoy"] = percentages[1]
        elif percentages:
            if has_ytd and not has_report:
                data["ytd_yoy"] = percentages[0]
            else:
                data["report_yoy"] = percentages[0]

        return data

    def _build_compare_company_slots(self, entity: str, metric_terms: list[str], message: str, retrieval_result) -> dict[str, str]:
        missing = self._evidence_missing_text()
        slots = {
            "entity": entity,
            "source_path": missing,
            "locator": missing,
            "report_value": missing,
            "report_yoy": missing,
            "ytd_value": missing,
            "ytd_yoy": missing,
            "evidence_line": missing,
        }

        for candidate in self._collect_metric_candidates(entity, metric_terms, message, retrieval_result):
            line = str(candidate["line"])
            parsed = self._parse_period_values_from_line(line, self._requested_period_scope(message))
            if slots["source_path"] == missing:
                slots["source_path"] = str(candidate["source_path"])
            if slots["locator"] == missing and str(candidate["locator"]).strip():
                slots["locator"] = str(candidate["locator"]).strip()
            if slots["evidence_line"] == missing:
                slots["evidence_line"] = line
            for field in ("report_value", "report_yoy", "ytd_value", "ytd_yoy"):
                if slots[field] == missing and parsed[field] != missing:
                    slots[field] = parsed[field]

        scope = self._requested_period_scope(message)
        requested_fields = ["report_value", "report_yoy"] if scope == "report" else ["ytd_value", "ytd_yoy"]
        if scope == "both":
            requested_fields = ["report_value", "report_yoy", "ytd_value", "ytd_yoy"]
        missing_fields = [field for field in requested_fields if slots[field] == missing]
        slots["missing_fields"] = ", ".join(missing_fields) if missing_fields else "none"
        return slots

    def _extract_named_items_from_corpus(self, support_corpus: str, message: str) -> list[tuple[str, list[str]]]:
        lines = [line.strip() for line in re.split(r"[\n\r]+", str(support_corpus or "")) if line.strip()]
        matches: dict[str, list[str]] = {}
        domain_terms = []
        if any(term in message for term in ("\u533b\u7597", "\u5c31\u533b", "\u533b\u9662", "\u533b\u4fdd")):
            domain_terms = ["health", "healthcare", "\u533b\u7597", "\u5c31\u533b", "\u533b\u9662", "\u533b\u4fdd"]

        quoted_pattern = re.compile(r"[\"“”']([^\"“”']{2,30})[\"“”']")
        english_pattern = re.compile(r"\b[A-Z][A-Za-z0-9.+-]*(?:\s+(?:for\s+)?[A-Z][A-Za-z0-9.+-]*)*\b")
        chinese_product_pattern = re.compile(r"([\u4e00-\u9fffA-Za-z0-9]{2,30}(?:\u667a\u80fd\u4f53|\u4ea7\u54c1|\u5e73\u53f0|\u52a9\u624b|\u7cfb\u7edf))")

        for line in lines:
            lowered = line.lower()
            if domain_terms and not any(term in lowered or term in line for term in domain_terms):
                continue
            candidates = [match.group(1).strip() for match in quoted_pattern.finditer(line)]
            candidates.extend(match.group(0).strip() for match in english_pattern.finditer(line))
            candidates.extend(match.group(1).strip() for match in chinese_product_pattern.finditer(line))
            for candidate in candidates:
                if len(candidate) < 2 or candidate.lower() in {"q3", "ai"}:
                    continue
                if candidate in {
                    "\u533b\u7597\u76f8\u5173\u4ea7\u54c1",
                    "\u533b\u7597\u4ea7\u54c1",
                    "\u4ea7\u54c1",
                    "\u667a\u80fd\u4f53",
                    "\u5c31\u533b\u667a\u80fd\u4f53",
                }:
                    continue
                matches.setdefault(candidate, []).append(line)

        return [(name, self._dedupe_preserve_order(item_lines)[:2]) for name, item_lines in matches.items()]

    def _collect_negation_evidence_lines(self, retrieval_result) -> tuple[list[str], list[str]]:
        support_corpus = self._knowledge_support_corpus(retrieval_result)
        evidence_lines = [raw.strip() for raw in re.split(r"[\n\r]+", support_corpus) if raw.strip()]
        direct: list[str] = []
        weak: list[str] = []
        profit_terms = (
            "\u51c0\u5229\u6da6",
            "\u5229\u6da6\u603b\u989d",
            "\u5f52\u5c5e\u4e8e\u4e0a\u5e02\u516c\u53f8\u80a1\u4e1c\u7684\u51c0\u5229\u6da6",
            "\u6263\u975e\u51c0\u5229\u6da6",
        )
        direct_terms = ("\u4e8f\u635f", "\u4e3a\u8d1f", "\u672a\u76c8\u5229", "\u8d1f\u503c", "\u51c0\u4e8f\u635f")
        for line in evidence_lines:
            has_profit_term = any(term in line for term in profit_terms)
            has_direct_term = any(term in line for term in direct_terms)
            has_negative_number = bool(re.search(r"-\d", line))
            has_zero_fragment = bool(re.search(r"(?<!\d)0(?:\.0+)?(?!\d)", line))
            has_not_applicable = "\u4e0d\u9002\u7528" in line

            if has_direct_term or (has_profit_term and has_negative_number):
                direct.append(line)
                continue
            if has_profit_term and (has_zero_fragment or has_not_applicable or "%" in line):
                weak.append(line)
        return self._dedupe_preserve_order(direct), self._dedupe_preserve_order(weak)

    def _metric_terms_from_query(self, message: str) -> tuple[str, list[str]]:
        lowered = str(message or "").lower()
        if "净利润" in message or "profit" in lowered:
            return "净利润", ["净利润", "归属于上市公司股东的净利润", "归母净利润", "扣非净利润"]
        if "营业收入" in message or "营收" in message or "revenue" in lowered:
            return "营业收入", ["营业收入", "营业总收入", "营收", "收入"]
        return "关键指标", ["净利润", "营业收入", "利润总额", "同比", "增长", "下降"]

    def _evidence_text_for_entity(self, entity: str, retrieval_result) -> str:
        blocks: list[str] = []
        for evidence in getattr(retrieval_result, "evidences", []) or []:
            blob = " ".join(
                [
                    str(getattr(evidence, "source_path", "") or ""),
                    str(getattr(evidence, "locator", "") or ""),
                    str(getattr(evidence, "snippet", "") or ""),
                ]
            )
            if entity.lower() in blob.lower():
                blocks.append(blob)
        return "\n".join(blocks)

    def _metric_focused_text(self, text: str, metric_terms: list[str]) -> str:
        lines = [line.strip() for line in re.split(r"[\n\r]+", str(text or "")) if line.strip()]
        focused = [line for line in lines if any(term.lower() in line.lower() for term in metric_terms)]
        return "\n".join(focused) if focused else str(text or "")

    def _extract_first_metric_amount(self, text: str) -> str:
        matches = re.findall(r"-?\d[\d,]*(?:\.\d+)?\s*(?:亿元|万元|元)", str(text or ""))
        return matches[0].strip() if matches else "当前证据未显示"

    def _extract_first_percentage(self, text: str) -> str:
        matches = re.findall(r"-?\d[\d,]*(?:\.\d+)?\s*%", str(text or ""))
        return matches[0].strip() if matches else "当前证据未显示"

    def _extract_missing_compare_fields(self, value: str, yoy: str) -> list[str]:
        missing: list[str] = []
        if value == "当前证据未显示":
            missing.append("绝对数值")
        if yoy == "当前证据未显示":
            missing.append("同比变化")
        return missing

    def _build_compare_scaffold(self, message: str, retrieval_result) -> str:
        entities = self._knowledge_entities(message, retrieval_result)
        if len(entities) < 2:
            return ""

        metric_label, metric_terms = self._metric_terms_from_query(message)
        period = "2025 Q3"
        if "年初至报告期末" in message:
            period = "年初至报告期末"
        elif "本报告期" in message or "单季" in message:
            period = "本报告期"

        slots: list[str] = ["[Compare answer scaffold]"]
        slots.append(f"metric: {metric_label}")
        slots.append(f"period: {period}")
        for index, entity in enumerate(entities, start=1):
            entity_text = self._metric_focused_text(self._evidence_text_for_entity(entity, retrieval_result), metric_terms)
            value = self._extract_first_metric_amount(entity_text)
            yoy = self._extract_first_percentage(entity_text)
            missing_fields = ", ".join(self._extract_missing_compare_fields(value, yoy)) or "无"
            slots.extend(
                [
                    f"company_{'a' if index == 1 else 'b'}: {entity}",
                    f"value_{'a' if index == 1 else 'b'}: {value}",
                    f"yoy_{'a' if index == 1 else 'b'}: {yoy}",
                    f"missing_fields_{'a' if index == 1 else 'b'}: {missing_fields}",
                ]
            )
        slots.append("Rules: copy these slot values directly into the answer; do not recompute or swap values across companies or periods.")
        slots.append("Rules: compare company by company; if one company lacks a field, write 当前证据未显示 for that company only.")
        return "\n".join(slots)

    def _build_multi_hop_scaffold(self, message: str, retrieval_result) -> str:
        support_corpus = self._knowledge_support_corpus(retrieval_result)
        lowered = str(message or "").lower()
        constraints: list[tuple[str, list[str]]] = []
        if any(term in message for term in ("业绩情况", "净利润", "营收", "营业收入")):
            constraints.append(("constraint_1", ["净利润", "营业收入", "利润总额", "同比", "增长", "下降"]))
        if any(term in message for term in ("原因", "所致", "损失", "既", "又")) or "reason" in lowered:
            constraints.append(("constraint_2", ["原因", "所致", "损失", "影响", "导致", "索赔"]))
        if not constraints:
            return ""

        lines = ["[Multi-hop answer scaffold]"]
        missing: list[str] = []
        for label, terms in constraints[:2]:
            matched_lines = [
                raw.strip()
                for raw in re.split(r"[\n\r]+", support_corpus)
                if raw.strip() and any(term.lower() in raw.lower() for term in terms)
            ]
            if matched_lines:
                lines.append(f"{label}: covered")
                lines.append(f"{label}_evidence: {' | '.join(matched_lines[:3])}")
            else:
                lines.append(f"{label}: missing")
                missing.append(label)
        if missing:
            lines.append(f"missing_constraints: {', '.join(missing)}")
        else:
            lines.append("missing_constraints: none")
        lines.append("Rules: cover each constraint separately; if any constraint is missing, keep the answer partial and say 当前证据未显示 for that missing part.")
        lines.append("Rules: stay within the explicitly requested products, entities, and evidence; do not add extra examples, companies, or products.")
        return "\n".join(lines)

    def _build_negation_scaffold(self, message: str, retrieval_result) -> str:
        support_corpus = self._knowledge_support_corpus(retrieval_result)
        evidence_lines = [
            raw.strip()
            for raw in re.split(r"[\n\r]+", support_corpus)
            if raw.strip()
        ]
        numeric_lines = [
            line
            for line in evidence_lines
            if any(token in line for token in ("-", "%", "元", "亿元", "万元"))
            or any(term in line for term in ("净利润", "利润总额", "营业收入", "亏损", "为负"))
        ]
        lines = ["[Negation answer scaffold]"]
        if numeric_lines:
            lines.append("direct_negative_evidence: present")
            lines.append(f"evidence_lines: {' | '.join(numeric_lines[:4])}")
        else:
            lines.append("direct_negative_evidence: missing")
            lines.append("evidence_lines: 当前证据未显示直接的亏损或负值条目")
        lines.append("Rules: do not mention retrieval status, internal pipeline notes, or hidden system reasons.")
        lines.append("Rules: if direct negative evidence is missing, say only that the current evidence is insufficient to confirm the negative conclusion.")
        return "\n".join(lines)

    def _build_compare_scaffold(self, message: str, retrieval_result) -> str:  # type: ignore[override]
        entities = self._knowledge_entities(message, retrieval_result)
        if len(entities) < 2:
            return ""

        metric_label, metric_terms = self._metric_terms_from_query(message)
        slots: list[str] = ["[Compare answer scaffold]"]
        slots.append(f"metric: {metric_label}")
        slots.append(f"period_scope: {self._requested_period_scope(message)}")
        for index, entity in enumerate(entities, start=1):
            company_slots = self._build_compare_company_slots(entity, metric_terms, message, retrieval_result)
            prefix = "a" if index == 1 else "b"
            slots.extend(
                [
                    f"company_{prefix}: {company_slots['entity']}",
                    f"source_{prefix}: {company_slots['source_path']}",
                    f"locator_{prefix}: {company_slots['locator']}",
                    f"report_value_{prefix}: {company_slots['report_value']}",
                    f"report_yoy_{prefix}: {company_slots['report_yoy']}",
                    f"ytd_value_{prefix}: {company_slots['ytd_value']}",
                    f"ytd_yoy_{prefix}: {company_slots['ytd_yoy']}",
                    f"evidence_line_{prefix}: {company_slots['evidence_line']}",
                    f"missing_fields_{prefix}: {company_slots['missing_fields']}",
                ]
            )
        slots.append("Rules: treat these slots as authoritative extracted facts from the current evidence.")
        slots.append("Rules: do not swap company A/B, do not mix report period and year-to-date, and do not turn yoy into an absolute value.")
        slots.append(f"Rules: if a slot is missing, keep it as {self._evidence_missing_text()} for that company only; do not generalize that the whole knowledge base lacks the field.")
        return "\n".join(slots)

    def _build_multi_hop_scaffold(self, message: str, retrieval_result) -> str:  # type: ignore[override]
        support_corpus = self._knowledge_support_corpus(retrieval_result)
        lowered = str(message or "").lower()
        requested_two_items = any(token in message for token in ("\u4e24\u9879", "\u4e24\u4e2a", "2\u9879", "2\u4e2a"))
        extracted_items = self._extract_named_items_from_corpus(support_corpus, message)

        if requested_two_items and extracted_items:
            lines = ["[Multi-hop answer scaffold]"]
            lines.append("mode: enumerated_items")
            lines.append("requested_item_count: 2")
            lines.append(f"found_item_count: {min(len(extracted_items), 2)}")
            for index, (name, evidence_lines) in enumerate(extracted_items[:2], start=1):
                lines.append(f"item_{index}: {name}")
                lines.append(f"item_{index}_evidence: {' | '.join(evidence_lines[:2])}")
            lines.append(f"missing_constraints: {'item_2' if len(extracted_items) < 2 else 'none'}")
            lines.append(f"Rules: list up to two distinct supported items. If fewer than two are supported, keep the answer partial and mark the missing item as {self._evidence_missing_text()}.")
            lines.append("Rules: do not collapse two different products into a single generic summary.")
            return "\n".join(lines)

        constraints: list[tuple[str, list[str], str]] = []
        if any(term in message for term in ("\u4e1a\u7ee9\u60c5\u51b5", "\u51c0\u5229\u6da6", "\u8425\u6536", "\u8425\u4e1a\u6536\u5165")):
            constraints.append(("constraint_1", ["\u51c0\u5229\u6da6", "\u8425\u4e1a\u6536\u5165", "\u5229\u6da6\u603b\u989d", "\u540c\u6bd4", "\u589e\u957f", "\u4e0b\u964d"], "performance"))
        if any(term in message for term in ("\u539f\u56e0", "\u6240\u81f4", "\u635f\u5931", "\u53ca", "\u53c8")) or "reason" in lowered:
            constraints.append(("constraint_2", ["\u539f\u56e0", "\u6240\u81f4", "\u635f\u5931", "\u5f71\u54cd", "\u5bfc\u81f4", "\u7d22\u8d54"], "reason"))
        if not constraints:
            return ""

        lines = ["[Multi-hop answer scaffold]"]
        missing: list[str] = []
        for label, terms, kind in constraints[:2]:
            matched_lines = [
                raw.strip()
                for raw in re.split(r"[\n\r]+", support_corpus)
                if raw.strip() and any(term.lower() in raw.lower() for term in terms)
            ]
            if matched_lines:
                lines.append(f"{label}: covered")
                lines.append(f"{label}_kind: {kind}")
                lines.append(f"{label}_evidence: {' | '.join(matched_lines[:3])}")
            else:
                lines.append(f"{label}: missing")
                lines.append(f"{label}_kind: {kind}")
                missing.append(label)
        lines.append(f"missing_constraints: {', '.join(missing) if missing else 'none'}")
        lines.append(f"Rules: satisfy every listed constraint separately. If any constraint is missing, keep the answer partial and write {self._evidence_missing_text()} for the uncovered part.")
        lines.append("Rules: do not replace the requested reason with a generic explanation that is not supported by the evidence.")
        return "\n".join(lines)

    def _build_negation_scaffold(self, message: str, retrieval_result) -> str:  # type: ignore[override]
        direct_lines, weak_lines = self._collect_negation_evidence_lines(retrieval_result)
        lines = ["[Negation answer scaffold]"]
        if direct_lines:
            lines.append("negative_signal: direct")
            lines.append(f"direct_evidence_lines: {' | '.join(direct_lines[:4])}")
        elif weak_lines:
            lines.append("negative_signal: weak")
            lines.append(f"weak_fragment_lines: {' | '.join(weak_lines[:4])}")
        else:
            lines.append("negative_signal: missing")
            lines.append(f"weak_fragment_lines: {self._evidence_missing_text()}")
        lines.append("Rules: do not mention retrieval status, internal pipeline notes, or hidden system reasons.")
        lines.append("Rules: only conclude loss / negative profit / not profitable when negative_signal is direct.")
        lines.append(f"Rules: if negative_signal is weak, mention the fragmentary indicator but say it is insufficient to prove the stronger conclusion; never say the evidence contains only title information when weak fragments are present.")
        return "\n".join(lines)

    def _build_knowledge_scaffold(self, message: str, retrieval_result) -> str:
        question_type = self._knowledge_question_type(retrieval_result)
        if question_type == "compare":
            return self._build_compare_scaffold(message, retrieval_result)
        if question_type == "multi_hop":
            return self._build_multi_hop_scaffold(message, retrieval_result)
        if question_type == "negation":
            return self._build_negation_scaffold(message, retrieval_result)
        return ""

    def _knowledge_answer_instructions(self, retrieval_result) -> list[str]:
        instructions = [
            "Use only the provided evidence and scaffold.",
            "Cite the source paths you used.",
            "Do not inspect files with tools or mention internal retrieval steps.",
            "Only state numbers or locators that appear directly in the evidence.",
            "If evidence is incomplete, answer conservatively and mark unsupported parts as 当前证据未显示.",
        ]
        if getattr(retrieval_result, "status", "") == "partial":
            instructions.extend(
                [
                    "The evidence is partial.",
                    "State supported findings first, then state what is still unsupported.",
                ]
            )
        question_type = self._knowledge_question_type(retrieval_result)
        if question_type == "compare":
            instructions.extend(
                [
                    "For compare questions, keep company, field, and period aligned.",
                    "Use the scaffold slot values verbatim when you write any table or bullet list.",
                    "Do not create a second summary table with recomputed values that differ from the scaffold.",
                    "Do not turn yoy percentages into absolute values or vice versa.",
                    "If one company has a missing slot, keep that company-level field as 当前证据未显示 instead of saying the whole knowledge base lacks it.",
                ]
            )
        if question_type == "multi_hop":
            instructions.extend(
                [
                    "For multi-hop questions, cover each requested constraint separately.",
                    "If one required constraint is missing, keep the answer partial.",
                    "If the scaffold enumerates multiple requested items, mention each supported item separately and do not pretend a missing second item is covered.",
                ]
            )
        if question_type == "negation":
            instructions.extend(
                [
                    "For negation questions, only conclude losses or negative values when evidence shows them directly.",
                    "If direct negative evidence exists, quote that supported value instead of summarizing it away.",
                    "If the scaffold marks weak fragments only, mention those fragmentary indicators but say they are insufficient for the stronger negative conclusion.",
                    "Do not say the evidence contains only title information when the scaffold already includes financial fragments.",
                ]
            )
        return instructions

    def _knowledge_support_corpus(self, retrieval_result) -> str:
        if retrieval_result is None or not getattr(retrieval_result, "evidences", None):
            return ""
        blocks: list[str] = []
        for evidence in retrieval_result.evidences:
            parts = [str(getattr(evidence, "source_path", "")).strip()]
            locator = str(getattr(evidence, "locator", "")).strip()
            snippet = str(getattr(evidence, "snippet", "")).strip()
            if locator:
                parts.append(locator)
            if snippet:
                parts.append(snippet)
            blocks.append("\n".join(part for part in parts if part))
        return "\n\n".join(blocks)

    def _dedupe_preserve_order(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            candidate = str(value).strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                deduped.append(candidate)
        return deduped

    def _extract_sensitive_numeric_tokens(self, answer: str) -> list[str]:
        pattern = re.compile(
            r"(?:¥|￥|\$)?-?\d[\d,]*(?:\.\d+)?(?:%|％|元|万元|亿元|万亿|亿|万|百万元|千万元|million|billion)?",
            re.IGNORECASE,
        )
        tokens: list[str] = []
        for match in pattern.finditer(str(answer or "")):
            token = str(match.group(0)).strip()
            if not token:
                continue
            has_unit = bool(re.search(r"[%％元亿万美元¥￥$]|million|billion", token, re.IGNORECASE))
            has_precision = any(marker in token for marker in [",", "."])
            digit_count = len(re.sub(r"\D+", "", token))
            if has_unit or has_precision or digit_count >= 4:
                tokens.append(token)
        return self._dedupe_preserve_order(tokens)

    def _extract_locator_tokens(self, answer: str) -> list[str]:
        patterns = (
            r"第\s*\d+\s*页",
            r"页\s*\d+",
            r"第\s*\d+\s*段",
            r"段落\s*\d+",
            r"section\s*[a-z0-9.\-]+",
            r"page\s*\d+",
            r"paragraph\s*\d+",
            r"table\s*\d+",
            r"row\s*\d+",
            r"column\s*\d+",
            r"表\s*\d+",
            r"行\s*\d+",
            r"列\s*\d+",
        )
        tokens: list[str] = []
        answer_text = str(answer or "")
        for pattern in patterns:
            tokens.extend(match.group(0).strip() for match in re.finditer(pattern, answer_text, re.IGNORECASE))
        return self._dedupe_preserve_order(tokens)

    def _unsupported_high_risk_inference_terms(self, answer: str, support_corpus: str) -> list[str]:
        terms = (
            "亏损",
            "未盈利",
            "不盈利",
            "负值",
            "负数",
            "净利润为负",
            "利润为负",
            "盈利为负",
        )
        answer_text = str(answer or "")
        unsupported: list[str] = []
        for term in terms:
            if term in answer_text and not self._detail_supported_by_corpus(term, support_corpus):
                unsupported.append(term)
        return self._dedupe_preserve_order(unsupported)

    def _detail_supported_by_corpus(self, token: str, support_corpus: str) -> bool:
        raw_token = str(token).strip()
        raw_corpus = str(support_corpus or "")
        if not raw_token:
            return True
        if raw_token in raw_corpus:
            return True
        canonical_token = _canonical_guard_text(raw_token)
        canonical_corpus = _canonical_guard_text(raw_corpus)
        if canonical_token and canonical_token in canonical_corpus:
            return True
        compact_token = _compact_guard_text(raw_token)
        compact_corpus = _compact_guard_text(raw_corpus)
        return bool(compact_token and compact_token in compact_corpus)

    def _unsupported_knowledge_details(self, answer: str, support_corpus: str) -> dict[str, list[str]]:
        numeric_tokens = self._extract_sensitive_numeric_tokens(answer)
        locator_tokens = self._extract_locator_tokens(answer)
        unsupported_numbers = [
            token for token in numeric_tokens if not self._detail_supported_by_corpus(token, support_corpus)
        ]
        unsupported_locators = [
            token for token in locator_tokens if not self._detail_supported_by_corpus(token, support_corpus)
        ]
        return {
            "numbers": self._dedupe_preserve_order(unsupported_numbers),
            "locators": self._dedupe_preserve_order(unsupported_locators),
        }

    def _all_sources_are_directory_guides(self, retrieval_result) -> bool:
        evidences = list(getattr(retrieval_result, "evidences", []) or [])
        if not evidences:
            return False
        source_paths = [str(getattr(item, "source_path", "")).replace("\\", "/").lower() for item in evidences]
        return all(path.endswith("data_structure.md") for path in source_paths if path)

    def _build_conservative_knowledge_answer(
        self,
        retrieval_result,
        *,
        unsupported_numbers: list[str] | None = None,
        unsupported_locators: list[str] | None = None,
    ) -> str:
        unsupported_numbers = unsupported_numbers or []
        unsupported_locators = unsupported_locators or []
        source_paths = self._dedupe_preserve_order(
            [str(getattr(item, "source_path", "")).strip() for item in getattr(retrieval_result, "evidences", []) or []]
        )

        lines: list[str] = []
        status = str(getattr(retrieval_result, "status", "") or "").strip().lower()
        if status == "success":
            lines.append("当前知识库命中了相关来源，但现有证据片段不足以支持更具体的数字或定位信息。")
        elif status == "partial":
            lines.append("当前知识库仅支持部分回答。")
        else:
            lines.append("当前知识库未检到足够证据。")

        if unsupported_numbers or unsupported_locators:
            lines.append("当前未检到可直接支持具体财务数字、百分比、金额或页码/段落号等定位信息的证据。")

        reason = str(getattr(retrieval_result, "reason", "") or "").strip()
        if reason:
            lines.append(reason)

        if source_paths:
            lines.append("已命中的来源路径：")
            lines.extend(f"- {path}" for path in source_paths[:6])
        else:
            lines.append("当前没有可引用的直接来源路径。")

        return "\n".join(lines).strip()

    async def _collect_model_answer(
        self,
        messages: list[dict[str, str]],
        extra_instructions: list[str] | None = None,
    ) -> str:
        final_content = ""
        async for event in self._astream_model_answer(messages, extra_instructions=extra_instructions):
            if event.get("type") == "done":
                final_content = str(event.get("content", "") or "").strip()
        return final_content

    def _guard_knowledge_answer(self, answer: str, retrieval_result) -> str:
        if not str(answer or "").strip():
            return self._build_conservative_knowledge_answer(retrieval_result)
        support_corpus = self._knowledge_support_corpus(retrieval_result)
        unsupported = self._unsupported_knowledge_details(answer, support_corpus)
        unsupported_numbers = unsupported["numbers"]
        unsupported_locators = unsupported["locators"]
        if unsupported_numbers or unsupported_locators:
            return self._build_conservative_knowledge_answer(
                retrieval_result,
                unsupported_numbers=unsupported_numbers,
                unsupported_locators=unsupported_locators,
            )

        unsupported_inference_terms = self._unsupported_high_risk_inference_terms(answer, support_corpus)
        if unsupported_inference_terms:
            return self._build_conservative_knowledge_answer(retrieval_result)

        if getattr(retrieval_result, "status", "") in {"partial", "not_found"} and self._all_sources_are_directory_guides(retrieval_result):
            return self._build_conservative_knowledge_answer(retrieval_result)

        return answer

    def _tool_agent_instructions(self, strategy: ExecutionStrategy, skill_decision: SkillDecision | None = None) -> list[str]:
        """Return tool-agent instructions from one execution strategy input."""

        instructions = [
            "If you use any tool, you must always produce a final natural-language answer for the user after the tool results arrive.",
            "Do not stop at an action announcement such as saying you will use a tool.",
            "When tool output is sufficient, summarize the result directly and clearly.",
        ]
        if skill_decision and skill_decision.use_skill and skill_decision.skill_name:
            instructions.extend(skill_instruction(skill_decision.skill_name))
        instructions.extend(strategy.to_instructions())
        return instructions

    def _tool_results_context(self, recorded_tools: list[dict[str, str]]) -> str:
        """Return one compact tool-result context block from recorded tool calls."""

        blocks = ["[Tool execution results]"]
        for index, item in enumerate(recorded_tools, start=1):
            output = str(item.get("output", "")).strip()
            truncated_output = output[:2000] + ("..." if len(output) > 2000 else "")
            blocks.append(
                f"{index}. Tool: {item.get('tool', 'tool')}\n"
                f"Input: {item.get('input', '')}\n"
                f"Output:\n{truncated_output or '[no output]'}"
            )
        return "\n\n".join(blocks)

    def _needs_tool_result_fallback(self, final_content: str, recorded_tools: list[dict[str, str]]) -> bool:
        """Return whether tool results need a fallback final answer."""

        if not recorded_tools:
            return False
        if not final_content.strip():
            return True
        lowered = final_content.strip().lower()
        if any(pattern.search(final_content.strip()) for pattern in ACTION_ONLY_PATTERNS):
            return True
        if lowered in {"thinking...", "working on it...", "processing..."}:
            return True
        return False

    async def _astream_tool_result_fallback(
        self,
        history_messages: list[dict[str, str]],
        user_message: str,
        recorded_tools: list[dict[str, str]],
        strategy: ExecutionStrategy,
    ):
        """Yield a fallback natural-language answer from completed tool results."""

        fallback_messages = list(history_messages)
        fallback_messages.append({"role": "assistant", "content": self._tool_results_context(recorded_tools)})
        fallback_messages.append({"role": "user", "content": user_message})

        fallback_instructions = [
            "The tool calls already succeeded. Do not call more tools.",
            "Answer the user's original request directly using the provided tool results.",
            "Your answer must be natural-language and user-facing, not an internal note.",
        ]
        fallback_instructions.extend(strategy.to_instructions())

        yielded_token = False
        async for event in self._astream_model_answer(fallback_messages, extra_instructions=fallback_instructions):
            if event.get("type") == "token" and str(event.get("content", "")).strip():
                yielded_token = True
            if event.get("type") == "done" and not str(event.get("content", "")).strip():
                continue
            yield event

        if yielded_token:
            return

        compact_lines = []
        for item in recorded_tools:
            output = str(item.get("output", "")).strip()
            if output:
                compact_lines.append(output[:1200])

        fallback_text = "根据已成功执行的工具结果，我整理如下：\n\n" + "\n\n".join(compact_lines[:3])
        yield {"type": "done", "content": fallback_text.strip()}

    async def _astream_model_answer(
        self,
        messages: list[dict[str, str]],
        extra_instructions: list[str] | None = None,
        system_prompt_override: str | None = None,
    ):
        if self.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")

        system_prompt = system_prompt_override or build_system_prompt(self.base_dir, runtime_config.get_rag_mode())
        if extra_instructions:
            system_prompt = f"{system_prompt}\n\n" + "\n\n".join(extra_instructions)

        model_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        model_messages.extend(messages)
        prompt_payload = _serialize_model_messages(model_messages)
        prompt_tokens = count_tokens(prompt_payload)

        final_content_parts: list[str] = []
        async for chunk in self._build_chat_model().astream(model_messages):
            text = _stringify_content(getattr(chunk, "content", ""))
            if text:
                final_content_parts.append(text)
                yield {"type": "token", "content": text}

        final_content = "".join(final_content_parts).strip()
        yield {
            "type": "done",
            "content": final_content,
            "usage": {
                "input_tokens": prompt_tokens,
                "output_tokens": count_tokens(final_content),
            },
        }

    async def astream(
        self,
        message: str,
        history: list[dict[str, Any]],
    ):
        if self.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")

        strategy, routing_decision = await self.resolve_routing(message, history)
        skill_decision = self.decide_skill(message, history, strategy, routing_decision)
        rag_mode = runtime_config.get_rag_mode()
        augmented_history = list(history)
        if rag_mode and strategy.allow_retrieval:
            retrievals = memory_indexer.retrieve(message, top_k=3)
            if retrievals:
                yield {"type": "retrieval", **self._format_memory_retrieval_step(retrievals)}
            if retrievals:
                augmented_history.append(
                    {
                        "role": "assistant",
                        "content": self._format_retrieval_context(retrievals),
                    }
                )

        if routing_decision.intent == "direct_answer" or (not routing_decision.needs_tools and not routing_decision.needs_retrieval):
            messages = self._build_messages(augmented_history)
            messages.append({"role": "user", "content": message})
            async for event in self._astream_model_answer(
                messages,
                extra_instructions=strategy.to_instructions(),
            ):
                yield event
            return

        if routing_decision.intent == "knowledge_qa" and strategy.allow_knowledge and strategy.allow_retrieval:
            knowledge_result = None
            async for event in knowledge_orchestrator.astream(message):
                if event.get("type") == "orchestrated_result":
                    knowledge_result = event["result"]
                    continue
                yield event

            if knowledge_result is not None:
                for step in knowledge_result.steps:
                    yield {"type": "retrieval", **step.to_dict()}
                augmented_history.append(
                    {
                        "role": "assistant",
                        "content": self._format_knowledge_context(knowledge_result),
                    }
                )
                scaffold = self._build_knowledge_scaffold(message, knowledge_result)
                if scaffold:
                    augmented_history.append(
                        {
                            "role": "assistant",
                            "content": scaffold,
                        }
                    )

            messages = self._build_messages(augmented_history)
            messages.append({"role": "user", "content": message})

            async for event in self._astream_model_answer(
                messages,
                extra_instructions=self._knowledge_answer_instructions(knowledge_result) if knowledge_result else None,
                system_prompt_override=build_knowledge_system_prompt(),
            ):
                yield event
            return

        allowed_tools = self._resolve_tools_for_strategy(strategy)
        if routing_decision.allowed_tools:
            allowed_names = set(routing_decision.allowed_tools)
            allowed_tools = [tool for tool in allowed_tools if getattr(tool, "name", "") in allowed_names]
        if skill_decision.use_skill:
            yield {"type": "skill_gate", "decision": skill_decision.to_dict()}
        if not allowed_tools:
            messages = self._build_messages(augmented_history)
            messages.append({"role": "user", "content": message})
            async for event in self._astream_model_answer(
                messages,
                extra_instructions=strategy.to_instructions(),
            ):
                yield event
            return

        agent = self._build_agent(
            extra_instructions=self._tool_agent_instructions(strategy, skill_decision),
            tools_override=allowed_tools,
        )
        messages = self._build_messages(augmented_history)
        messages.append({"role": "user", "content": message})

        final_content_parts: list[str] = []
        last_ai_message = ""
        last_streamed_model_text = ""
        pending_tools: dict[str, dict[str, str]] = {}
        recorded_tools: list[dict[str, str]] = []

        async for mode, payload in agent.astream(
            {"messages": messages},
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                chunk, metadata = payload
                if metadata.get("langgraph_node") != "model":
                    continue
                text = _stringify_content(getattr(chunk, "content", ""))
                next_chunk = _incremental_text(last_streamed_model_text, text)
                if text:
                    last_streamed_model_text = text
                if next_chunk:
                    final_content_parts.append(next_chunk)
                    yield {"type": "token", "content": next_chunk}
                continue

            if mode != "updates":
                continue

            for update in payload.values():
                for agent_message in update.get("messages", []):
                    message_type = getattr(agent_message, "type", "")
                    tool_calls = getattr(agent_message, "tool_calls", []) or []

                    if message_type == "ai" and not tool_calls:
                        candidate = _stringify_content(getattr(agent_message, "content", ""))
                        if candidate:
                            last_ai_message = candidate

                    if tool_calls:
                        for tool_call in tool_calls:
                            call_id = str(tool_call.get("id") or tool_call.get("name"))
                            tool_name = str(tool_call.get("name", "tool"))
                            tool_args = tool_call.get("args", "")
                            if not isinstance(tool_args, str):
                                tool_args = json.dumps(tool_args, ensure_ascii=False)
                            pending_tools[call_id] = {
                                "tool": tool_name,
                                "input": str(tool_args),
                            }
                            yield {
                                "type": "tool_start",
                                "tool": tool_name,
                                "input": str(tool_args),
                            }

                    if message_type == "tool":
                        tool_call_id = str(getattr(agent_message, "tool_call_id", ""))
                        pending = pending_tools.pop(
                            tool_call_id,
                            {"tool": getattr(agent_message, "name", "tool"), "input": ""},
                        )
                        output = _stringify_content(getattr(agent_message, "content", ""))
                        recorded_tools.append(
                            {
                                "tool": pending["tool"],
                                "input": pending["input"],
                                "output": output,
                            }
                        )
                        yield {
                            "type": "tool_end",
                            "tool": pending["tool"],
                            "output": output,
                        }
                        yield {"type": "new_response"}

        final_content = "".join(final_content_parts).strip() or last_ai_message.strip()
        if self._needs_tool_result_fallback(final_content, recorded_tools):
            async for event in self._astream_tool_result_fallback(
                self._build_messages(augmented_history),
                message,
                recorded_tools,
                strategy,
            ):
                yield event
            return
        yield {"type": "done", "content": final_content}

    async def generate_title(self, first_user_message: str) -> str:
        prompt = (
            "请根据用户的第一条消息生成一个中文会话标题。"
            "要求不超过 10 个汉字，不要带引号，不要解释。"
        )
        try:
            response = await self._build_chat_model().ainvoke(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": first_user_message},
                ]
            )
            title = _stringify_content(getattr(response, "content", "")).strip()
            return title[:10] or "新会话"
        except Exception:
            return (first_user_message.strip() or "新会话")[:10]

    async def summarize_history(self, messages: list[dict[str, Any]]) -> str:
        prompt = (
            "请将以下对话压缩成中文摘要，控制在 500 字以内。"
            "重点保留用户目标、已完成步骤、重要结论和未解决事项。"
        )
        lines: list[str] = []
        for item in messages:
            role = item.get("role", "assistant")
            content = str(item.get("content", "") or "")
            if content:
                lines.append(f"{role}: {content}")
        transcript = "\n".join(lines)

        try:
            response = await self._build_chat_model().ainvoke(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": transcript},
                ]
            )
            summary = _stringify_content(getattr(response, "content", "")).strip()
            return summary[:500]
        except Exception:
            return transcript[:500]


agent_manager = AgentManager()
