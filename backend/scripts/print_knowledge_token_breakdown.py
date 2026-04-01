from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import get_settings, runtime_config
from graph.agent import AgentManager, _stringify_content, parse_execution_strategy
from graph.memory_indexer import memory_indexer
from graph.prompt_builder import SYSTEM_COMPONENTS, build_knowledge_system_prompt, build_system_prompt
from knowledge_retrieval import knowledge_indexer, knowledge_orchestrator
from knowledge_retrieval.evidence_organizer import source_family
from knowledge_retrieval.query_rewrite import build_query_plan
from token_utils import count_message_usage, count_tokens
from tools.skills_scanner import refresh_snapshot


DEFAULT_QUESTION = "知识库搜索三一重工和航天之间的财报比较"
QUESTION_PRESETS: dict[str, str] = {
    "compare": DEFAULT_QUESTION,
    "multi_hop": "根据知识库，哪份财报既显示净利润为负，又解释了业绩变化原因？请给出来源路径。",
    "negation": "根据知识库，说明航天动力 2025 Q3 并未盈利的证据，并给出来源。",
    "cross_file_aggregation": "根据知识库，横向比较三一重工、上汽集团、航天动力三家公司的 2025 Q3 业绩，应检索哪些财报路径？",
    "fuzzy": "根据知识库，哪份财报更像是在讲第三季度业绩承压但仍有关键财务指标披露？请给出来源路径。",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print token breakdown for one or more knowledge questions.")
    parser.add_argument("--question", default=DEFAULT_QUESTION, help="One explicit question to run.")
    parser.add_argument(
        "--question-type",
        choices=sorted(QUESTION_PRESETS),
        help="Run the preset question for one question_type.",
    )
    parser.add_argument(
        "--all-question-types",
        action="store_true",
        help="Run the built-in compare / multi_hop / negation / cross_file / fuzzy presets.",
    )
    return parser.parse_args()


def _format_count(label: str, text: str) -> str:
    return f"{label}: chars={len(text)} tokens={count_tokens(text)}"


def _serialize_messages(messages: list[dict[str, str]]) -> str:
    blocks: list[str] = []
    for item in messages:
        role = str(item.get("role", "")).strip() or "unknown"
        content = str(item.get("content", "") or "")
        blocks.append(f"{role}: {content}")
    return "\n\n".join(blocks)


def _serialize_retrieval_step(step: dict) -> str:
    return str(step)


def _component_breakdown(base_dir: Path) -> list[tuple[str, str, str]]:
    settings = get_settings()
    rows: list[tuple[str, str, str]] = []
    for label, relative_path in SYSTEM_COMPONENTS:
        path = base_dir / relative_path
        if not path.exists():
            rows.append((label, relative_path, "[missing]"))
            continue
        text = path.read_text(encoding="utf-8")
        truncated = text[: settings.component_char_limit]
        rows.append((label, relative_path, truncated))
    return rows


def _question_sequence(args: argparse.Namespace) -> list[tuple[str, str]]:
    if args.all_question_types:
        return list(QUESTION_PRESETS.items())
    if args.question_type:
        return [(args.question_type, QUESTION_PRESETS[args.question_type])]
    return [(build_query_plan(args.question).question_type, args.question)]


async def _profile_question(manager: AgentManager, question_type_label: str, question: str) -> None:
    strategy = parse_execution_strategy(question)
    rag_mode = runtime_config.get_rag_mode()
    augmented_history: list[dict[str, str]] = []

    memory_context = ""
    if rag_mode and strategy.allow_retrieval:
        memory_hits = memory_indexer.retrieve(question, top_k=3)
        if memory_hits:
            memory_context = manager._format_retrieval_context(memory_hits)
            augmented_history.append({"role": "assistant", "content": memory_context})

    knowledge_result = knowledge_orchestrator._build_formal_retrieval_result(question)
    knowledge_context = manager._format_knowledge_context(knowledge_result)
    augmented_history.append({"role": "assistant", "content": knowledge_context})

    scaffold = manager._build_knowledge_scaffold(question, knowledge_result)
    if scaffold:
        augmented_history.append({"role": "assistant", "content": scaffold})

    extra_instructions_list = manager._knowledge_answer_instructions(knowledge_result)
    extra_instructions = "\n\n".join(extra_instructions_list)
    knowledge_system_prompt = build_knowledge_system_prompt()
    merged_system_prompt = (
        f"{knowledge_system_prompt}\n\n{extra_instructions}" if extra_instructions else knowledge_system_prompt
    )

    messages = manager._build_messages(augmented_history)
    messages.append({"role": "user", "content": question})
    model_messages = [{"role": "system", "content": merged_system_prompt}, *messages]
    final_message_payload = _serialize_messages(model_messages)

    answer_parts: list[str] = []
    async for chunk in manager._build_chat_model().astream(model_messages):
        text = _stringify_content(getattr(chunk, "content", ""))
        if text:
            answer_parts.append(text)
    answer = "".join(answer_parts).strip()

    retrieval_steps = [step.to_dict() for step in knowledge_result.steps]
    retrieval_trace_text = "\n\n".join(_serialize_retrieval_step(step) for step in retrieval_steps)
    frontend_equivalent_message_tokens = count_tokens(
        "\n".join(
            part
            for part in [
                question,
                answer,
                *(_serialize_retrieval_step(step) for step in retrieval_steps),
            ]
            if part
        )
    )
    frontend_equivalent_total_tokens = count_tokens(build_system_prompt(get_settings().backend_dir, runtime_config.get_rag_mode())) + frontend_equivalent_message_tokens

    print("=" * 80)
    print(f"question_type: {question_type_label}")
    print(f"detected_question_type: {getattr(knowledge_result, 'question_type', 'direct_fact')}")
    print(f"question: {question}")
    print()
    print(_format_count("system prompt", knowledge_system_prompt))
    if memory_context:
        print(_format_count("memory context", memory_context))
    print(_format_count("knowledge context", knowledge_context))
    print(_format_count("scaffold", scaffold))
    print(_format_count("extra instructions", extra_instructions))
    print(_format_count("final message total", final_message_payload))
    print(_format_count("answer", answer))
    print(_format_count("retrieval trace total", retrieval_trace_text))
    print(f"final model call total tokens: {count_tokens(final_message_payload) + count_tokens(answer)}")
    print(f"frontend-equivalent session total tokens: {frontend_equivalent_total_tokens}")
    print()
    print("knowledge context evidences:")
    for index, evidence in enumerate(getattr(knowledge_result, "evidences", []) or [], start=1):
        snippet = str(getattr(evidence, "snippet", "") or "")
        print(
            f"- #{index} family={source_family(getattr(evidence, 'source_path', ''))} "
            f"source={getattr(evidence, 'source_path', '')} "
            f"page={getattr(evidence, 'page', None)} "
            f"type={getattr(evidence, 'element_type', None)} "
            f"children={getattr(evidence, 'supporting_children', 1) or 1} "
            f"chars={len(snippet)} tokens={count_tokens(snippet)}"
        )
    print()
    print("system prompt components (full prompt reference only):")
    for label, relative_path, content in _component_breakdown(get_settings().backend_dir):
        print(f"- {label} [{relative_path}]: chars={len(content)} tokens={count_tokens(content)}")
    print()
    print("knowledge retrieval:")
    print(f"- status: {knowledge_result.status}")
    print(f"- question_type: {getattr(knowledge_result, 'question_type', 'direct_fact')}")
    print(f"- sources: {', '.join(item.source_path for item in knowledge_result.evidences) or '[none]'}")
    print(f"- assistant message usage tokens (content + retrieval steps): {count_message_usage(answer, retrieval_steps=retrieval_steps)}")
    print("- retrieval stages:")
    for step in retrieval_steps:
        stage_text = _serialize_retrieval_step(step)
        print(
            f"  - {step.get('stage', 'unknown')}: chars={len(stage_text)} tokens={count_tokens(stage_text)} "
            f"results={len(step.get('results', []) or [])}"
        )
    print()
    print("answer:")
    print(answer or "[empty answer]")
    print()


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    base_dir = settings.backend_dir

    refresh_snapshot(base_dir)

    manager = AgentManager()
    manager.initialize(base_dir)
    memory_indexer.configure(base_dir)
    memory_indexer.rebuild_index()
    knowledge_indexer.configure(base_dir)
    knowledge_indexer.warm_start()
    knowledge_orchestrator.configure(base_dir, manager._build_chat_model)

    for question_type_label, question in _question_sequence(args):
        await _profile_question(manager, question_type_label, question)
    return 0


def main() -> int:
    return asyncio.run(_run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
