from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from src.backend.decision.execution_strategy import ExecutionStrategy
from src.backend.decision.prompt_builder import build_system_prompt
from src.backend.decision.skill_gate import SkillDecision, skill_instruction, skill_prompt_cards
from src.backend.runtime.token_utils import count_tokens

if TYPE_CHECKING:  # pragma: no cover
    from src.backend.runtime.agent_manager import AgentManager


ACTION_ONLY_PATTERNS = (
    re.compile(
        r"^(?:i'll|i will|let me)\s+(?:use|call).{0,40}(?:tool|terminal|python_repl|read_file|fetch_url|mcp_filesystem_read_file|mcp_filesystem_list_directory|filesystem mcp)",
        re.IGNORECASE,
    ),
)
ACTION_ONLY_PREFIXES = (
    "我来使用",
    "让我使用",
    "我会使用",
    "我将使用",
    "Let me use Filesystem MCP",
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


def incremental_text(previous: str, current: str) -> str:
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


class HarnessExecutionSupport:
    """Harness-owned helpers for model and tool execution, separate from graph routing helpers."""

    def __init__(self, agent_manager: "AgentManager") -> None:
        self._agent = agent_manager

    def build_tool_agent(
        self,
        *,
        extra_instructions: list[str] | None = None,
        tools_override: list[Any] | None = None,
    ):
        if self._agent.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")

        from langchain.agents import create_agent  # pylint: disable=import-outside-toplevel

        system_prompt = build_system_prompt(self._agent.base_dir, self._agent._runtime_rag_mode())
        if extra_instructions:
            system_prompt = f"{system_prompt}\n\n" + "\n\n".join(extra_instructions)
        return create_agent(
            model=self._agent._build_chat_model(),
            tools=self._agent.tools if tools_override is None else tools_override,
            system_prompt=system_prompt,
        )

    def incremental_stream_text(self, previous: str, current: str) -> str:
        return incremental_text(previous, current)

    async def astream_model_answer(
        self,
        messages: list[dict[str, str]],
        extra_instructions: list[str] | None = None,
        system_prompt_override: str | None = None,
    ):
        if self._agent.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")

        system_prompt = system_prompt_override or build_system_prompt(
            self._agent.base_dir,
            self._agent._runtime_rag_mode(),
        )
        if extra_instructions:
            system_prompt = f"{system_prompt}\n\n" + "\n\n".join(extra_instructions)

        model_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        model_messages.extend(messages)
        prompt_payload = _serialize_model_messages(model_messages)
        prompt_tokens = count_tokens(prompt_payload)

        final_content_parts: list[str] = []
        last_streamed = ""
        async for chunk in self._agent._build_chat_model().astream(model_messages):
            text = _stringify_content(getattr(chunk, "content", ""))
            if text:
                next_chunk = self.incremental_stream_text(last_streamed, text)
                last_streamed = text
                if next_chunk:
                    final_content_parts.append(next_chunk)
                    yield {"type": "token", "content": next_chunk}

        final_content = "".join(final_content_parts).strip()
        yield {
            "type": "done",
            "content": final_content,
            "usage": {
                "input_tokens": prompt_tokens,
                "output_tokens": count_tokens(final_content),
            },
        }

    def capability_decision_cards(self) -> list[str]:
        cards = ["Capability guide:"]
        cards.append(
            "- Direct answer: use when the request can be solved from reasoning or rewriting without external state."
        )
        cards.append(
            "- Knowledge retrieval: use for indexed reports, grounded source lookup, comparisons, or evidence-bound report questions."
        )
        for spec in self._agent.get_capability_registry().list(enabled_only=True):
            cards.append(
                f"- {spec.capability_id} ({spec.capability_type}): {spec.when_to_use} "
                f"Do not use when {spec.when_not_to_use} Risk={spec.risk_level}."
            )
        cards.append("- If a tool or retrieval result is partial or noisy, answer conservatively and only reflect what it actually supports.")
        cards.extend(skill_prompt_cards())
        return cards

    def tool_agent_instructions(
        self,
        strategy: ExecutionStrategy,
        skill_decision: SkillDecision | None = None,
    ) -> list[str]:
        instructions = [
            "If you use any tool, you must always produce a final natural-language answer for the user after the tool results arrive.",
            "Do not stop at an action announcement such as saying you will use a tool.",
            "When tool output is sufficient, summarize the result directly and clearly.",
            "Choose the narrowest useful capability instead of opening extra tools or switching problem types.",
        ]
        instructions.extend(self.capability_decision_cards())
        if skill_decision and skill_decision.use_skill and skill_decision.skill_name:
            instructions.extend(skill_instruction(skill_decision.skill_name))
        instructions.extend(strategy.to_instructions())
        return instructions

    def tool_results_context(self, recorded_tools: list[dict[str, str]]) -> str:
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

    def needs_tool_result_fallback(self, final_content: str, recorded_tools: list[dict[str, str]]) -> bool:
        if not recorded_tools:
            return False
        candidate = str(final_content or "").strip()
        if not candidate:
            return True
        lowered = candidate.lower()
        if any(candidate.startswith(prefix) for prefix in ACTION_ONLY_PREFIXES):
            return True
        if any(pattern.search(candidate) for pattern in ACTION_ONLY_PATTERNS):
            return True
        return lowered in {"thinking...", "working on it...", "processing..."}
