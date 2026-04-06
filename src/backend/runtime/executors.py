"""Execution adapters that let the harness runtime drive the existing Ragclaw capabilities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator

from src.backend.capabilities.invocation import (
    CapabilityRuntimeContext,
    GovernedCapabilityTool,
    capability_runtime_scope,
    invoke_capability,
)
from src.backend.capabilities.types import CapabilityResult
from src.backend.decision.execution_strategy import ExecutionStrategy
from src.backend.decision.skill_gate import SkillDecision, skill_instruction
from src.backend.knowledge import knowledge_orchestrator
from src.backend.knowledge.memory_indexer import memory_indexer
from src.backend.observability.types import (
    AnswerRecord,
    RetrievalRecord,
    RouteDecisionRecord,
    SkillDecisionRecord,
    ToolCallRecord,
)
from src.backend.runtime.graders import KnowledgeAnswerGrader

if TYPE_CHECKING:  # pragma: no cover
    from src.backend.decision.lightweight_router import RoutingDecision
    from src.backend.runtime.agent_manager import AgentManager
    from src.backend.runtime.runtime import HarnessRuntime, RuntimeRunHandle


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


_EXPLICIT_MCP_PATTERNS = (
    re.compile(r"\bfilesystem mcp\b", re.IGNORECASE),
    re.compile(r"\bmcp filesystem\b", re.IGNORECASE),
)
_EXPLICIT_WEB_MCP_PATTERNS = (
    re.compile(r"\bweb mcp\b", re.IGNORECASE),
    re.compile(r"\bdocument fetch mcp\b", re.IGNORECASE),
)
_REPEATED_MCP_PATTERNS = (
    re.compile(r"\b(?:twice|three times|repeat(?:ed)?|again|\d+\s+times)\b", re.IGNORECASE),
    re.compile(r"(?:两次|三次|重复|再来一次)"),
)
_READ_PATH_PATTERNS = (
    re.compile(r"\bread\s+([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+)", re.IGNORECASE),
    re.compile(r"\bopen\s+([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+)", re.IGNORECASE),
)
_LIST_PATH_PATTERNS = (
    re.compile(r"\blist\s+([A-Za-z0-9_./\\-]+)", re.IGNORECASE),
    re.compile(r"\bshow\s+([A-Za-z0-9_./\\-]+)", re.IGNORECASE),
)
_FETCH_URL_PATTERNS = (
    re.compile(r"\bfetch\s+(https?://[^\s]+)", re.IGNORECASE),
    re.compile(r"\bread\s+(https?://[^\s]+)", re.IGNORECASE),
    re.compile(r"\bvisit\s+(https?://[^\s]+)", re.IGNORECASE),
)


@dataclass
class RunSummaryState:
    route_intent: str = ""
    used_skill: str = ""
    final_answer: str = ""
    tool_names: list[str] | None = None
    retrieval_sources: list[str] | None = None

    def __post_init__(self) -> None:
        if self.tool_names is None:
            self.tool_names = []
        if self.retrieval_sources is None:
            self.retrieval_sources = []

    def add_tool(self, tool_name: str) -> None:
        tool = str(tool_name or "").strip()
        if tool and tool not in self.tool_names:
            self.tool_names.append(tool)

    def add_retrieval_sources(self, sources: list[str]) -> None:
        for source in sources:
            candidate = str(source or "").strip()
            if candidate and candidate not in self.retrieval_sources:
                self.retrieval_sources.append(candidate)


class HarnessExecutors:
    """Thin adapter over AgentManager so harness owns lifecycle while reusing existing capabilities."""

    def __init__(self, agent_manager: "AgentManager") -> None:
        self._agent = agent_manager
        self._execution = agent_manager.create_execution_support()
        self._knowledge_grader = KnowledgeAnswerGrader(agent_manager)

    async def execute(
        self,
        runtime: "HarnessRuntime",
        handle: "RuntimeRunHandle",
        *,
        message: str,
        history: list[dict[str, Any]],
    ) -> RunSummaryState:
        state = RunSummaryState()
        context = CapabilityRuntimeContext(
            runtime=runtime,
            handle=handle,
            registry=self._agent.get_capability_registry(),
            governor=runtime.governor_for(handle.run_id),
        )

        async with capability_runtime_scope(context):
            strategy, routing_decision = await self._agent.resolve_routing(message, history)
            state.route_intent = routing_decision.intent
            await runtime.emit(
                handle,
                "route.decided",
                RouteDecisionRecord(
                    intent=routing_decision.intent,
                    needs_tools=routing_decision.needs_tools,
                    needs_retrieval=routing_decision.needs_retrieval,
                    allowed_tools=tuple(routing_decision.allowed_tools),
                    confidence=routing_decision.confidence,
                    reason_short=routing_decision.reason_short,
                    source=routing_decision.source,
                    subtype=routing_decision.subtype,
                    ambiguity_flags=tuple(routing_decision.ambiguity_flags),
                    escalated=routing_decision.escalated,
                    model_name=routing_decision.model_name,
                ).to_dict(),
            )

            skill_decision = self._agent.decide_skill(message, history, strategy, routing_decision)
            if skill_decision.use_skill:
                state.used_skill = skill_decision.skill_name
                await self._activate_skill_capability(
                    message=message,
                    routing_decision=routing_decision,
                    skill_decision=skill_decision,
                )
            await runtime.emit(
                handle,
                "skill.decided",
                SkillDecisionRecord(
                    use_skill=skill_decision.use_skill,
                    skill_name=skill_decision.skill_name,
                    confidence=skill_decision.confidence,
                    reason_short=skill_decision.reason_short,
                ).to_dict(),
            )

            rag_mode = self._agent._runtime_rag_mode()
            augmented_history = list(history)
            if rag_mode and strategy.allow_retrieval:
                await runtime.emit(
                    handle,
                    "retrieval.started",
                    {"kind": "memory", "stage": "memory", "title": "Memory retrieval", "message": ""},
                )
                retrievals = memory_indexer.retrieve(message, top_k=3)
                if retrievals:
                    memory_step = self._agent._format_memory_retrieval_step(retrievals)
                    await runtime.emit(
                        handle,
                        "retrieval.completed",
                        RetrievalRecord(
                            kind=memory_step["kind"],
                            stage=memory_step["stage"],
                            title=memory_step["title"],
                            message=memory_step["message"],
                            results=tuple(self._agent._harness_retrieval_evidence_records(memory_step["results"])),
                        ).to_dict(),
                    )
                    state.add_retrieval_sources([item["source_path"] for item in memory_step["results"]])
                    augmented_history.append(
                        {"role": "assistant", "content": self._agent._format_retrieval_context(retrievals)}
                    )

            if routing_decision.intent == "direct_answer" or (
                not routing_decision.needs_tools and not routing_decision.needs_retrieval
            ):
                messages = self._agent._build_messages(augmented_history)
                messages.append({"role": "user", "content": message})
                final_answer, _usage = await self._stream_model_answer(
                    runtime,
                    handle,
                    messages,
                    extra_instructions=strategy.to_instructions(),
                )
                state.final_answer = final_answer
                return state

            if routing_decision.intent == "knowledge_qa" and strategy.allow_knowledge and strategy.allow_retrieval:
                await runtime.emit(
                    handle,
                    "retrieval.started",
                    {"kind": "knowledge", "stage": "knowledge", "title": "Knowledge retrieval", "message": ""},
                )
                knowledge_result = None
                async for event in knowledge_orchestrator.astream(message):
                    if event.get("type") == "orchestrated_result":
                        knowledge_result = event["result"]
                        continue

                if knowledge_result is not None:
                    for step in knowledge_result.steps:
                        await runtime.emit(
                            handle,
                            "retrieval.completed",
                            RetrievalRecord(
                                kind=step.kind,
                                stage=step.stage,
                                title=step.title,
                                message=step.message,
                                results=tuple(self._agent._harness_retrieval_evidence_records([item.to_dict() for item in step.results])),
                                status=getattr(knowledge_result, "status", ""),
                                reason=getattr(knowledge_result, "reason", ""),
                            ).to_dict(),
                        )
                        state.add_retrieval_sources([item.source_path for item in step.results])
                    augmented_history.append(
                        {"role": "assistant", "content": self._agent._format_knowledge_context(knowledge_result)}
                    )
                    scaffold = self._agent._build_knowledge_scaffold(message, knowledge_result)
                    if scaffold:
                        augmented_history.append({"role": "assistant", "content": scaffold})

                messages = self._agent._build_messages(augmented_history)
                messages.append({"role": "user", "content": message})
                answer, usage = await self._stream_model_answer(
                    runtime,
                    handle,
                    messages,
                    extra_instructions=self._agent._knowledge_answer_instructions(knowledge_result) if knowledge_result else None,
                    system_prompt_override=self._agent._knowledge_system_prompt(),
                    stream_deltas=False,
                )
                final_answer = answer
                if knowledge_result is not None:
                    guard_decision = self._knowledge_grader.grade(answer, knowledge_result)
                    final_answer = guard_decision.final_answer
                    if guard_decision.guard_result is not None:
                        await runtime.emit(
                            handle,
                            "guard.failed",
                            guard_decision.guard_result.to_dict(),
                        )
                await runtime.emit(
                    handle,
                    "answer.started",
                    AnswerRecord(content="", segment_index=runtime.current_segment_index(handle), final=False).to_dict(),
                )
                if final_answer:
                    await runtime.emit(
                        handle,
                        "answer.delta",
                        AnswerRecord(
                            content=final_answer,
                            segment_index=runtime.current_segment_index(handle),
                            final=False,
                        ).to_dict(),
                    )
                await runtime.emit(
                    handle,
                    "answer.completed",
                    AnswerRecord(
                        content=final_answer,
                        segment_index=runtime.current_segment_index(handle),
                        final=True,
                        input_tokens=int(usage.get("input_tokens", 0) or 0) if isinstance(usage, dict) and usage.get("input_tokens") is not None else None,
                        output_tokens=int(usage.get("output_tokens", 0) or 0) if isinstance(usage, dict) and usage.get("output_tokens") is not None else None,
                    ).to_dict(),
                )
                state.final_answer = final_answer
                return state

            allowed_tools = self._agent._resolve_tools_for_strategy(strategy)
            if routing_decision.allowed_tools:
                allowed_names = set(routing_decision.allowed_tools)
                allowed_tools = [tool for tool in allowed_tools if getattr(tool, "name", "") in allowed_names]
            if not allowed_tools:
                messages = self._agent._build_messages(augmented_history)
                messages.append({"role": "user", "content": message})
                final_answer, _usage = await self._stream_model_answer(
                    runtime,
                    handle,
                    messages,
                    extra_instructions=strategy.to_instructions(),
                )
                state.final_answer = final_answer
                return state

            explicit_mcp_answer = await self._maybe_execute_explicit_mcp_path(
                runtime,
                handle,
                message=message,
                allowed_tools=allowed_tools,
                state=state,
            )
            if explicit_mcp_answer is not None:
                state.final_answer = explicit_mcp_answer
                return state

            final_answer = await self._stream_tool_path(
                runtime,
                handle,
                message=message,
                augmented_history=augmented_history,
                strategy=strategy,
                skill_decision=skill_decision,
                allowed_tools=allowed_tools,
                state=state,
            )
            state.final_answer = final_answer
            return state

    async def _maybe_execute_explicit_mcp_path(
        self,
        runtime: "HarnessRuntime",
        handle: "RuntimeRunHandle",
        *,
        message: str,
        allowed_tools: list[Any],
        state: RunSummaryState,
    ) -> str | None:
        normalized_message = str(message or "")
        if not any(pattern.search(normalized_message) for pattern in (*_EXPLICIT_MCP_PATTERNS, *_EXPLICIT_WEB_MCP_PATTERNS)):
            return None
        if any(pattern.search(normalized_message) for pattern in _REPEATED_MCP_PATTERNS):
            return None
        if len(allowed_tools) != 1:
            return None

        tool = allowed_tools[0]
        if not isinstance(tool, GovernedCapabilityTool):
            return None

        tool_name = str(getattr(tool, "name", "") or "")
        if tool_name not in {"mcp_filesystem_read_file", "mcp_filesystem_list_directory", "mcp_web_fetch_url"}:
            return None

        payload = self._explicit_mcp_payload(tool_name, normalized_message)
        if payload is None:
            return None

        call_id = f"explicit-{tool_name}"
        serialized_input = json.dumps(payload, ensure_ascii=False)
        state.add_tool(tool_name)
        await runtime.emit(
            handle,
            "tool.started",
            ToolCallRecord(tool=tool_name, input=serialized_input, call_id=call_id).to_dict(),
        )
        result = await tool.aexecute_capability(payload)
        rendered = tool.render_capability_result(result)
        await runtime.emit(
            handle,
            "tool.completed",
            ToolCallRecord(
                tool=tool_name,
                input=serialized_input,
                output=rendered,
                call_id=call_id,
            ).to_dict(),
        )
        await runtime.emit(
            handle,
            "answer.started",
            AnswerRecord(content="", segment_index=runtime.current_segment_index(handle), final=False).to_dict(),
        )
        if rendered:
            await runtime.emit(
                handle,
                "answer.delta",
                AnswerRecord(content=rendered, segment_index=runtime.current_segment_index(handle), final=False).to_dict(),
            )
        await runtime.emit(
            handle,
            "answer.completed",
            AnswerRecord(content=rendered, segment_index=runtime.current_segment_index(handle), final=True).to_dict(),
        )
        return rendered

    def _explicit_mcp_payload(self, tool_name: str, message: str) -> dict[str, str] | None:
        if tool_name == "mcp_filesystem_read_file":
            patterns = _READ_PATH_PATTERNS
        elif tool_name == "mcp_filesystem_list_directory":
            patterns = _LIST_PATH_PATTERNS
        else:
            patterns = _FETCH_URL_PATTERNS
        for pattern in patterns:
            match = pattern.search(message)
            if match:
                key = "url" if tool_name == "mcp_web_fetch_url" else "path"
                return {key: str(match.group(1)).strip().rstrip(".,;:")}
        return None

    async def _activate_skill_capability(
        self,
        *,
        message: str,
        routing_decision: "RoutingDecision",
        skill_decision: SkillDecision,
    ) -> None:
        skill_key = skill_decision.skill_name.replace("-", "_")
        spec = self._agent.get_capability_registry().get(f"skill.{skill_key}")
        await invoke_capability(
            spec=spec,
            payload={
                "message": message,
                "allowed_capabilities": list(getattr(routing_decision, "allowed_tools", ()) or ()),
            },
            execute_async=self._build_skill_runner(spec, skill_decision),
        )

    def _build_skill_runner(self, spec, skill_decision: SkillDecision):
        async def _runner(_payload: dict[str, Any]) -> CapabilityResult:
            return CapabilityResult(
                status="success",
                payload={
                    "capability_id": spec.capability_id,
                    "guidance": skill_instruction(skill_decision.skill_name),
                    "reason_short": skill_decision.reason_short,
                    "confidence": skill_decision.confidence,
                },
                partial=False,
            )

        return _runner

    async def _stream_model_answer(
        self,
        runtime: "HarnessRuntime",
        handle: "RuntimeRunHandle",
        messages: list[dict[str, str]],
        *,
        extra_instructions: list[str] | None = None,
        system_prompt_override: str | None = None,
        stream_deltas: bool = True,
    ) -> tuple[str, dict[str, int] | None]:
        started = False
        final_answer = ""
        usage: dict[str, int] | None = None
        async for event in self._execution.astream_model_answer(
            messages,
            extra_instructions=extra_instructions,
            system_prompt_override=system_prompt_override,
        ):
            event_type = str(event.get("type", "") or "")
            if event_type == "token":
                if not started and stream_deltas:
                    await runtime.emit(
                        handle,
                        "answer.started",
                        AnswerRecord(content="", segment_index=runtime.current_segment_index(handle), final=False).to_dict(),
                    )
                    started = True
                content = str(event.get("content", "") or "")
                if content:
                    final_answer += content
                    if stream_deltas:
                        await runtime.emit(
                            handle,
                            "answer.delta",
                            AnswerRecord(content=content, segment_index=runtime.current_segment_index(handle), final=False).to_dict(),
                        )
            elif event_type == "done":
                final_answer = str(event.get("content", "") or "").strip() or final_answer.strip()
                usage = event.get("usage", None)
                if not started and stream_deltas:
                    await runtime.emit(
                        handle,
                        "answer.started",
                        AnswerRecord(content="", segment_index=runtime.current_segment_index(handle), final=False).to_dict(),
                    )
                    started = True
                if stream_deltas:
                    await runtime.emit(
                        handle,
                        "answer.completed",
                        AnswerRecord(
                            content=final_answer,
                            segment_index=runtime.current_segment_index(handle),
                            final=True,
                            input_tokens=int(usage.get("input_tokens", 0) or 0) if isinstance(usage, dict) and usage.get("input_tokens") is not None else None,
                            output_tokens=int(usage.get("output_tokens", 0) or 0) if isinstance(usage, dict) and usage.get("output_tokens") is not None else None,
                        ).to_dict(),
                    )
        return final_answer, usage

    async def _stream_tool_path(
        self,
        runtime: "HarnessRuntime",
        handle: "RuntimeRunHandle",
        *,
        message: str,
        augmented_history: list[dict[str, Any]],
        strategy: ExecutionStrategy,
        skill_decision: SkillDecision,
        allowed_tools: list[Any],
        state: RunSummaryState,
    ) -> str:
        agent = self._execution.build_tool_agent(
            extra_instructions=self._execution.tool_agent_instructions(strategy, skill_decision),
            tools_override=allowed_tools,
        )
        messages = self._agent._build_messages(augmented_history)
        messages.append({"role": "user", "content": message})

        final_content_parts: list[str] = []
        last_ai_message = ""
        last_streamed_model_text = ""
        pending_tools: dict[str, dict[str, str]] = {}
        recorded_tools: list[dict[str, str]] = []
        answer_started = False

        async for mode, payload in agent.astream({"messages": messages}, stream_mode=["messages", "updates"]):
            if mode == "messages":
                chunk, metadata = payload
                if metadata.get("langgraph_node") != "model":
                    continue
                text = _stringify_content(getattr(chunk, "content", ""))
                next_chunk = self._execution.incremental_stream_text(last_streamed_model_text, text)
                if text:
                    last_streamed_model_text = text
                if next_chunk:
                    if not answer_started:
                        await runtime.emit(
                            handle,
                            "answer.started",
                            AnswerRecord(content="", segment_index=runtime.current_segment_index(handle), final=False).to_dict(),
                        )
                        answer_started = True
                    final_content_parts.append(next_chunk)
                    await runtime.emit(
                        handle,
                        "answer.delta",
                        AnswerRecord(content=next_chunk, segment_index=runtime.current_segment_index(handle), final=False).to_dict(),
                    )
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
                            pending_tools[call_id] = {"tool": tool_name, "input": str(tool_args)}
                            state.add_tool(tool_name)
                            await runtime.emit(
                                handle,
                                "tool.started",
                                ToolCallRecord(tool=tool_name, input=str(tool_args), call_id=call_id).to_dict(),
                            )

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
                                "call_id": tool_call_id,
                            }
                        )
                        await runtime.emit(
                            handle,
                            "tool.completed",
                            ToolCallRecord(
                                tool=pending["tool"],
                                input=pending["input"],
                                output=output,
                                call_id=tool_call_id,
                            ).to_dict(),
                        )
                        runtime.advance_answer_segment(handle)
                        answer_started = False

        final_content = "".join(final_content_parts).strip() or last_ai_message.strip()
        if self._execution.needs_tool_result_fallback(final_content, recorded_tools):
            final_content = await self._stream_tool_result_fallback(
                runtime,
                handle,
                history_messages=self._agent._build_messages(augmented_history),
                user_message=message,
                recorded_tools=recorded_tools,
                strategy=strategy,
            )
        elif final_content or answer_started:
            await runtime.emit(
                handle,
                "answer.completed",
                AnswerRecord(
                    content=final_content,
                    segment_index=runtime.current_segment_index(handle),
                    final=True,
                ).to_dict(),
            )

        return final_content

    async def _stream_tool_result_fallback(
        self,
        runtime: "HarnessRuntime",
        handle: "RuntimeRunHandle",
        *,
        history_messages: list[dict[str, str]],
        user_message: str,
        recorded_tools: list[dict[str, str]],
        strategy: ExecutionStrategy,
    ) -> str:
        fallback_messages = list(history_messages)
        fallback_messages.append({"role": "assistant", "content": self._execution.tool_results_context(recorded_tools)})
        fallback_messages.append({"role": "user", "content": user_message})

        fallback_instructions = [
            "The tool calls already succeeded. Do not call more tools.",
            "Answer the user's original request directly using the provided tool results.",
            "Your answer must be natural-language and user-facing, not an internal note.",
        ]
        fallback_instructions.extend(strategy.to_instructions())

        answer, _usage = await self._stream_model_answer(
            runtime,
            handle,
            fallback_messages,
            extra_instructions=fallback_instructions,
        )
        if answer:
            return answer

        compact_lines = []
        for item in recorded_tools:
            output = str(item.get("output", "")).strip()
            if output:
                compact_lines.append(output[:1200])

        fallback_text = "根据已成功执行的工具结果，我整理如下：\n\n" + "\n\n".join(compact_lines[:3])
        await runtime.emit(
            handle,
            "answer.started",
            AnswerRecord(content="", segment_index=runtime.current_segment_index(handle), final=False).to_dict(),
        )
        await runtime.emit(
            handle,
            "answer.delta",
            AnswerRecord(content=fallback_text, segment_index=runtime.current_segment_index(handle), final=False).to_dict(),
        )
        await runtime.emit(
            handle,
            "answer.completed",
            AnswerRecord(content=fallback_text, segment_index=runtime.current_segment_index(handle), final=True).to_dict(),
        )
        return fallback_text
