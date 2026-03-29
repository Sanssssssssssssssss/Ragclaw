from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from config import get_settings, runtime_config
from graph.execution_strategy import ExecutionStrategy, parse_execution_strategy
from graph.memory_indexer import memory_indexer
from graph.prompt_builder import build_system_prompt
from graph.session_manager import SessionManager
from knowledge_retrieval import knowledge_orchestrator
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


class AgentManager:
    def __init__(self) -> None:
        self.base_dir: Path | None = None
        self.session_manager: SessionManager | None = None
        self.tools = []

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

    def _is_knowledge_query(self, message: str) -> bool:
        normalized = message.replace("\\", "/").strip()
        lowered = normalized.lower()
        if any(token in normalized for token in STABLE_KNOWLEDGE_QUERY_SUBSTRINGS):
            return True
        if any(pattern.search(normalized) for pattern in STABLE_KNOWLEDGE_QUERY_PATTERNS):
            return True
        return lowered.startswith("based on the knowledge") or lowered.startswith("from the knowledge")

    def _should_prefer_tool_agent(self, message: str, strategy: ExecutionStrategy) -> bool:
        """Return whether the request should bypass knowledge routing and go straight to tools."""

        if strategy.require_tool_use or strategy.allowed_tools:
            return True
        return any(pattern.search(message) for pattern in STABLE_WORKSPACE_OPERATION_PATTERNS)

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
            support_note = ""
            if getattr(evidence, "supporting_children", None):
                support_note = f" / merged children: {evidence.supporting_children}"
            lines.append(
                f"{index}. [{evidence.channel}] {evidence.source_path} ({evidence.locator}){support_note}\n{evidence.snippet}"
            )
        return "\n\n".join(lines)

    def _knowledge_question_type(self, retrieval_result) -> str:
        return str(getattr(retrieval_result, "question_type", "") or "direct_fact").strip().lower()

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
        entities = [item for item in getattr(retrieval_result, "entity_hints", []) or [] if len(str(item).strip()) >= 2][:2]
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
            "This is a knowledge-base question.",
            "Use only the provided knowledge retrieval evidence to answer.",
            "Do not perform additional knowledge-base inspection with tools.",
            "If the evidence is incomplete, explicitly say the current knowledge base only supports a partial answer or no direct answer.",
            "Do not fabricate facts.",
            "Cite the file paths you relied on.",
            "Only mention numeric values, percentages, amounts, page numbers, paragraph numbers, section identifiers, or table/row/column locations when those details appear directly in the retrieval evidence.",
            "If the retrieval evidence does not directly support a numeric or locator detail, say the current knowledge base does not contain enough evidence for that level of specificity.",
            "Do not infer numbers or locator details from file names, prior knowledge, or guesswork.",
            "Do not infer profitability, losses, negative values, growth rates, or trends from headings, dashes, placeholders, blank cells, or table structure alone.",
            "If the evidence only shows document structure or incomplete fragments, answer conservatively and stop at what is directly supported.",
            "Do not mention internal pipeline details such as vector retrieval, BM25, fusion, fallback logic, or retrieval stages in the final answer.",
        ]
        if getattr(retrieval_result, "status", "") == "partial":
            instructions.extend(
                [
                    "The retrieval status is partial.",
                    "Answer in a conservative style: first state the supported findings, then state what the current evidence still does not support.",
                    "Do not complete comparisons, negation claims, or cross-file aggregation beyond the cited evidence.",
                ]
            )
        question_type = self._knowledge_question_type(retrieval_result)
        if question_type == "compare":
            instructions.extend(
                [
                    "For compare questions, organize the answer by company and field before writing prose.",
                    "Do not generalize one company's missing field into a statement about both companies or the whole knowledge base.",
                    "Keep period and scope precise: do not mix 本报告期 with 年初至报告期末.",
                ]
            )
        if question_type == "multi_hop":
            instructions.extend(
                [
                    "For multi-hop questions, cover each requested constraint separately before writing the final summary.",
                    "If one required constraint is not directly supported, keep the answer partial and explicitly mark that missing piece as 当前证据未显示.",
                    "Do not replace a missing cause with a generic business explanation.",
                    "Do not add extra products, companies, or examples beyond the requested scope.",
                ]
            )
        if question_type == "negation":
            instructions.extend(
                [
                    "For negation questions, only conclude loss, non-profitability, or negative values when the retrieved evidence shows those facts directly.",
                    "Do not quote or paraphrase hidden retrieval notes, status labels, or internal reasoning.",
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

    def _tool_agent_instructions(self, strategy: ExecutionStrategy) -> list[str]:
        """Return tool-agent instructions from one execution strategy input."""

        instructions = [
            "If you use any tool, you must always produce a final natural-language answer for the user after the tool results arrive.",
            "Do not stop at an action announcement such as saying you will use a tool.",
            "When tool output is sufficient, summarize the result directly and clearly.",
        ]
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
    ):
        if self.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")

        system_prompt = build_system_prompt(self.base_dir, runtime_config.get_rag_mode())
        if extra_instructions:
            system_prompt = f"{system_prompt}\n\n" + "\n\n".join(extra_instructions)

        model_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        model_messages.extend(messages)

        final_content_parts: list[str] = []
        async for chunk in self._build_chat_model().astream(model_messages):
            text = _stringify_content(getattr(chunk, "content", ""))
            if text:
                final_content_parts.append(text)
                yield {"type": "token", "content": text}

        yield {"type": "done", "content": "".join(final_content_parts).strip()}

    async def astream(
        self,
        message: str,
        history: list[dict[str, Any]],
    ):
        if self.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")

        strategy = parse_execution_strategy(message)
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

        if strategy.force_direct_answer or not strategy.allow_tools:
            messages = self._build_messages(augmented_history)
            messages.append({"role": "user", "content": message})
            async for event in self._astream_model_answer(
                messages,
                extra_instructions=strategy.to_instructions(),
            ):
                yield event
            return

        if (
            strategy.allow_knowledge
            and not self._should_prefer_tool_agent(message, strategy)
            and self._is_knowledge_query(message)
        ):
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
            ):
                yield event
            return

        allowed_tools = self._resolve_tools_for_strategy(strategy)
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
            extra_instructions=self._tool_agent_instructions(strategy),
            tools_override=allowed_tools,
        )
        messages = self._build_messages(augmented_history)
        messages.append({"role": "user", "content": message})

        final_content_parts: list[str] = []
        last_ai_message = ""
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
                if text:
                    final_content_parts.append(text)
                    yield {"type": "token", "content": text}
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
