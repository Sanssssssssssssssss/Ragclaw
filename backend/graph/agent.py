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
        lines.append(f"Status: {retrieval_result.status}")
        if retrieval_result.reason:
            lines.append(f"Reason: {retrieval_result.reason}")
        if retrieval_result.fallback_used:
            lines.append("Fallback: skill evidence was insufficient, so vector/BM25 retrieval was used.")
        if not retrieval_result.evidences:
            lines.append("No direct evidence was found.")
            return "\n".join(lines)

        for index, evidence in enumerate(retrieval_result.evidences, start=1):
            lines.append(
                f"{index}. [{evidence.channel}] {evidence.source_path} ({evidence.locator})\n{evidence.snippet}"
            )
        return "\n\n".join(lines)

    def _knowledge_answer_instructions(self, retrieval_result) -> list[str]:
        instructions = [
            "This is a knowledge-base question.",
            "Use only the provided knowledge retrieval evidence to answer.",
            "Do not perform additional knowledge-base inspection with tools.",
            "If the evidence is incomplete, explicitly say the current knowledge base only supports a partial answer or no direct answer.",
            "Do not fabricate facts.",
            "When evidence is insufficient, suggest narrowing the scope by directory, file, keyword, field name, or time range.",
            "Cite the file paths you relied on.",
        ]
        if retrieval_result.reason:
            instructions.append(f"Current retrieval note: {retrieval_result.reason}")
        return instructions

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
