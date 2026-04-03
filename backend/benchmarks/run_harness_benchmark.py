from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graph.agent import AgentManager
from graph.execution_strategy import ExecutionStrategy
from graph.lightweight_router import RoutingDecision
from graph.skill_gate import SkillDecision
from harness.executors import HarnessExecutors
from harness.graders import KnowledgeAnswerGrader
from harness.policy import SessionSerialQueue
from harness.runtime import HarnessRuntime, RuntimeDependencies
from harness.trace_store import RunTraceStore
from knowledge_retrieval.types import Evidence, OrchestratedRetrievalResult, RetrievalStep


DEFAULT_OUTPUT_PATH = BACKEND_DIR / "storage" / "benchmarks" / "harness_benchmark_latest.json"


@dataclass(frozen=True)
class LifecycleCase:
    case_id: str
    scenario: str
    session_id: str
    expect_retrieval: bool = False
    expect_tool: bool = False
    expect_failure: bool = False
    expect_queue: bool = False
    expected_answer_fragment: str = ""


@dataclass(frozen=True)
class RouteSkillCase:
    case_id: str
    message: str
    expected_intent: str
    expected_skill_name: str = ""


@dataclass(frozen=True)
class GuardCase:
    case_id: str
    answer: str
    retrieval_result: Any
    expect_guard: bool
    expected_trigger: str = ""
    counts_numeric: bool = False
    counts_locator: bool = False


class _NoRouterAllowed:
    async def route(self, **_kwargs):
        raise AssertionError("routing benchmark unexpectedly required the LLM router")


class _BenchmarkToolChunk:
    def __init__(self, content: str) -> None:
        self.content = content


class _BenchmarkToolMessage:
    def __init__(self, *, message_type: str, content: str = "", tool_calls=None, tool_call_id: str = "", name: str = "") -> None:
        self.type = message_type
        self.content = content
        self.tool_calls = list(tool_calls or [])
        self.tool_call_id = tool_call_id
        self.name = name


class _BenchmarkToolAgent:
    def __init__(self, case_id: str) -> None:
        self._case_id = case_id

    async def astream(self, _inputs, stream_mode=None):
        call_id = f"{self._case_id}-tool"
        yield (
            "updates",
            {
                "tool_node": {
                    "messages": [
                        _BenchmarkToolMessage(
                            message_type="ai",
                            tool_calls=[{"id": call_id, "name": "terminal", "args": {"command": "Get-ChildItem"}}],
                        )
                    ]
                }
            },
        )
        yield (
            "updates",
            {
                "tool_node": {
                    "messages": [
                        _BenchmarkToolMessage(
                            message_type="tool",
                            content="a.txt\nb.txt",
                            tool_call_id=call_id,
                            name="terminal",
                        )
                    ]
                }
            },
        )


class BenchmarkAgentManager(AgentManager):
    """Deterministic capability provider used to exercise the real harness executor."""

    def __init__(self, case_specs: dict[str, LifecycleCase]) -> None:
        super().__init__()
        self._case_specs = case_specs
        self.tools = [SimpleNamespace(name="terminal"), SimpleNamespace(name="fetch_url")]

    def _spec_for_message(self, message: str) -> LifecycleCase:
        key = str(message or "").replace(" message", "").strip()
        return self._case_specs[key]

    async def resolve_routing(self, message: str, history: list[dict[str, Any]]) -> tuple[ExecutionStrategy, RoutingDecision]:
        spec = self._spec_for_message(message)
        if spec.scenario == "knowledge_qa":
            strategy = ExecutionStrategy(allow_tools=False, allow_knowledge=True, allow_retrieval=True)
            decision = RoutingDecision(
                intent="knowledge_qa",
                needs_tools=False,
                needs_retrieval=True,
                allowed_tools=(),
                confidence=1.0,
                reason_short=spec.scenario,
                source="benchmark",
                subtype="",
            )
            return strategy, decision
        if spec.scenario == "tool_path":
            strategy = ExecutionStrategy(allow_tools=True, allow_knowledge=False, allow_retrieval=False)
            decision = RoutingDecision(
                intent="workspace_file_ops",
                needs_tools=True,
                needs_retrieval=False,
                allowed_tools=("terminal",),
                confidence=1.0,
                reason_short=spec.scenario,
                source="benchmark",
                subtype="search_workspace_file",
            )
            return strategy, decision
        strategy = ExecutionStrategy(allow_tools=False, allow_knowledge=False, allow_retrieval=False, force_direct_answer=True)
        decision = RoutingDecision(
            intent="direct_answer",
            needs_tools=False,
            needs_retrieval=False,
            allowed_tools=(),
            confidence=1.0,
            reason_short=spec.scenario,
            source="benchmark",
            subtype="",
        )
        return strategy, decision

    def decide_skill(self, message: str, history: list[dict[str, Any]], strategy: ExecutionStrategy, routing_decision: RoutingDecision) -> SkillDecision:
        return SkillDecision(False, "", 0.0, "benchmark disables skills")

    def _runtime_rag_mode(self) -> bool:
        return False

    def _knowledge_system_prompt(self) -> str:
        return "Knowledge benchmark system prompt"

    async def _astream_model_answer(
        self,
        messages: list[dict[str, str]],
        extra_instructions: list[str] | None = None,
        system_prompt_override: str | None = None,
    ):
        user_message = str(messages[-1].get("content", "") if messages else "")
        spec = self._spec_for_message(user_message)
        if spec.expect_failure:
            raise RuntimeError(f"{spec.case_id} failed")

        if spec.scenario == "knowledge_qa":
            final_text = "knowledge_case answer"
        elif spec.scenario == "tool_path" and extra_instructions and any("tool calls already succeeded" in item.lower() for item in extra_instructions):
            final_text = "Found files: a.txt and b.txt."
        else:
            final_text = f"{spec.case_id} answer"

        if spec.case_id == "queue_holder":
            await asyncio.sleep(0.1)

        midpoint = max(1, len(final_text) // 2)
        yield {"type": "token", "content": final_text[:midpoint]}
        yield {"type": "token", "content": final_text[midpoint:]}
        yield {"type": "done", "content": final_text, "usage": {"input_tokens": 10, "output_tokens": 4}}

    def _resolve_tools_for_strategy(self, strategy: ExecutionStrategy) -> list[Any]:
        if not strategy.allow_tools:
            return []
        return [tool for tool in self.tools if getattr(tool, "name", "") == "terminal"]

    def _build_agent(self, extra_instructions=None, tools_override=None):
        return _BenchmarkToolAgent("tool_case")

    def _build_knowledge_scaffold(self, message: str, retrieval_result) -> str:
        return ""

    def _knowledge_answer_instructions(self, retrieval_result) -> list[str]:
        return ["Use the evidence only."]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a harness-native benchmark suite.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="JSON output path.")
    return parser.parse_args()


def _trace_event_names(trace: dict[str, Any]) -> list[str]:
    return [str(item.get("name", "")) for item in trace.get("events", [])]


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _evidence(source_path: str, locator: str, snippet: str):
    return SimpleNamespace(source_path=source_path, locator=locator, snippet=snippet)


def _retrieval_result(*, status: str = "success", question_type: str = "compare", evidences=None, reason: str = ""):
    return SimpleNamespace(
        status=status,
        question_type=question_type,
        evidences=list(evidences or []),
        reason=reason,
    )


async def _fake_knowledge_astream(message: str, case_specs: dict[str, LifecycleCase]):
    spec = case_specs[str(message or "").replace(" message", "").strip()]
    evidence = Evidence(
        source_path="knowledge/report.pdf",
        source_type="pdf",
        locator="page 1",
        snippet="knowledge_case answer",
        channel="fused",
        score=0.9,
    )
    step = RetrievalStep(
        kind="knowledge",
        stage="fused",
        title="Knowledge retrieval",
        message="benchmark retrieval",
        results=[evidence],
    )
    result = OrchestratedRetrievalResult(
        status="success",
        evidences=[evidence],
        steps=[step],
        reason="benchmark retrieval",
        question_type="direct_fact",
        entity_hints=["benchmark"],
    )
    yield {"type": "orchestrated_result", "result": result}


async def _run_lifecycle_suite() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        runtime = HarnessRuntime(
            RuntimeDependencies(
                trace_store=RunTraceStore(root / "runs"),
                queue=SessionSerialQueue(lambda: datetime.now(timezone.utc).isoformat()),
            )
        )

        direct_spec = LifecycleCase(
            case_id="direct_case",
            scenario="direct_answer",
            session_id="session-direct",
            expected_answer_fragment="direct_case answer",
        )
        knowledge_spec = LifecycleCase(
            case_id="knowledge_case",
            scenario="knowledge_qa",
            session_id="session-knowledge",
            expect_retrieval=True,
            expected_answer_fragment="knowledge_case answer",
        )
        tool_spec = LifecycleCase(
            case_id="tool_case",
            scenario="tool_path",
            session_id="session-tool",
            expect_tool=True,
            expected_answer_fragment="a.txt",
        )
        failure_spec = LifecycleCase(
            case_id="failure_case",
            scenario="direct_answer",
            session_id="session-failure",
            expect_failure=True,
        )
        queued_spec = LifecycleCase(
            case_id="queued_case",
            scenario="direct_answer",
            session_id="session-queued",
            expect_queue=True,
            expected_answer_fragment="queued_case answer",
        )
        queue_holder_spec = LifecycleCase(
            case_id="queue_holder",
            scenario="direct_answer",
            session_id="session-queued",
            expected_answer_fragment="queue_holder answer",
        )
        case_specs = {
            spec.case_id: spec
            for spec in [direct_spec, knowledge_spec, tool_spec, failure_spec, queued_spec, queue_holder_spec]
        }

        benchmark_agent = BenchmarkAgentManager(case_specs)
        executor = HarnessExecutors(benchmark_agent)

        async def _run_case(spec: LifecycleCase) -> str:
            with (
                patch("harness.executors.memory_indexer.retrieve", return_value=[]),
                patch("harness.executors.knowledge_orchestrator.astream", side_effect=lambda message: _fake_knowledge_astream(message, case_specs)),
            ):
                events = [
                    event
                    async for event in runtime.run_with_executor(
                        user_message=f"{spec.case_id} message",
                        session_id=spec.session_id,
                        source="benchmark",
                        executor=executor,
                        history=[],
                        suppress_failures=True,
                    )
                ]
            if not events:
                raise RuntimeError(f"no events produced for {spec.case_id}")
            return events[0].run_id

        direct_run_id = await _run_case(direct_spec)
        knowledge_run_id = await _run_case(knowledge_spec)
        tool_run_id = await _run_case(tool_spec)
        failure_run_id = await _run_case(failure_spec)

        first_queued_task = asyncio.create_task(_run_case(queue_holder_spec))
        await asyncio.sleep(0.02)
        second_queued_task = asyncio.create_task(_run_case(queued_spec))
        await first_queued_task
        queued_run_id = await second_queued_task

        specs = [direct_spec, knowledge_spec, tool_spec, failure_spec, queued_spec]
        run_ids = {
            direct_spec.case_id: direct_run_id,
            knowledge_spec.case_id: knowledge_run_id,
            tool_spec.case_id: tool_run_id,
            failure_spec.case_id: failure_run_id,
            queued_spec.case_id: queued_run_id,
        }

        cases: list[dict[str, Any]] = []
        for spec in specs:
            trace = runtime._deps.trace_store.read_trace(run_ids[spec.case_id])  # noqa: SLF001
            event_names = _trace_event_names(trace)
            outcome = trace.get("outcome") or {}
            final_answer = str(outcome.get("final_answer", "") or "")
            route_present = "route.decided" in event_names
            retrieval_present = "retrieval.started" in event_names and "retrieval.completed" in event_names
            tool_present = "tool.started" in event_names and "tool.completed" in event_names
            answer_present = "answer.completed" in event_names and bool(final_answer.strip())
            completion_ok = (
                ("run.failed" in event_names and outcome.get("status") == "failed")
                if spec.expect_failure
                else ("run.completed" in event_names and outcome.get("status") == "completed")
            )
            queue_ok = (("run.queued" in event_names and "run.dequeued" in event_names) if spec.expect_queue else True)
            reflection_ok = (spec.expected_answer_fragment in final_answer) if spec.expected_answer_fragment else True
            trace_complete = route_present and completion_ok and queue_ok and reflection_ok
            if not spec.expect_failure:
                trace_complete = trace_complete and answer_present
            if spec.expect_retrieval:
                trace_complete = trace_complete and retrieval_present
            if spec.expect_tool:
                trace_complete = trace_complete and tool_present
            cases.append(
                {
                    "case_id": spec.case_id,
                    "scenario": spec.scenario,
                    "run_id": run_ids[spec.case_id],
                    "route_trace_present": route_present,
                    "retrieval_trace_present": retrieval_present,
                    "tool_trace_present": tool_present,
                    "final_answer_present": answer_present,
                    "tool_result_reflected": reflection_ok,
                    "completion_integrity": completion_ok,
                    "queue_integrity": queue_ok,
                    "trace_completeness": trace_complete,
                    "event_names": event_names,
                    "outcome": outcome,
                }
            )

    summary = {
        "total_cases": len(cases),
        "route_trace_presence": round(_safe_rate(sum(1 for item in cases if item["route_trace_present"]), len(cases)), 4),
        "retrieval_trace_presence": round(
            _safe_rate(sum(1 for item in cases if item["scenario"] != "knowledge_qa" or item["retrieval_trace_present"]), len(cases)),
            4,
        ),
        "tool_trace_presence": round(
            _safe_rate(sum(1 for item in cases if item["scenario"] != "tool_path" or item["tool_trace_present"]), len(cases)),
            4,
        ),
        "final_answer_presence": round(_safe_rate(sum(1 for item in cases if item["final_answer_present"]), len(cases)), 4),
        "tool_result_to_final_answer_reflection": round(
            _safe_rate(sum(1 for item in cases if item["tool_result_reflected"]), len(cases)),
            4,
        ),
        "completion_integrity": round(_safe_rate(sum(1 for item in cases if item["completion_integrity"]), len(cases)), 4),
        "queue_integrity": round(_safe_rate(sum(1 for item in cases if item["queue_integrity"]), len(cases)), 4),
        "trace_completeness": round(_safe_rate(sum(1 for item in cases if item["trace_completeness"]), len(cases)), 4),
    }
    return {"summary": summary, "cases": cases}


async def _run_route_skill_suite() -> dict[str, Any]:
    agent = AgentManager()
    agent.tools = [
        SimpleNamespace(name="fetch_url"),
        SimpleNamespace(name="read_file"),
        SimpleNamespace(name="terminal"),
        SimpleNamespace(name="python_repl"),
    ]
    agent._lightweight_router = _NoRouterAllowed()

    cases = [
        RouteSkillCase(
            case_id="direct_route",
            message="Explain what TCP is in one paragraph. Do not use tools.",
            expected_intent="direct_answer",
        ),
        RouteSkillCase(
            case_id="knowledge_route",
            message="Based on the knowledge base, which report mentions SANY?",
            expected_intent="knowledge_qa",
        ),
        RouteSkillCase(
            case_id="workspace_route",
            message="Read backend/api/chat.py and summarize it.",
            expected_intent="workspace_file_ops",
        ),
        RouteSkillCase(
            case_id="web_route",
            message="Find the latest OpenAI news on the web.",
            expected_intent="web_lookup",
            expected_skill_name="web-search",
        ),
        RouteSkillCase(
            case_id="weather_route",
            message="Look up the weather forecast for London tomorrow online.",
            expected_intent="web_lookup",
            expected_skill_name="get_weather",
        ),
    ]

    results: list[dict[str, Any]] = []
    for case in cases:
        strategy, decision = await agent.resolve_routing(case.message, [])
        skill_decision = agent.decide_skill(case.message, [], strategy, decision)
        route_ok = decision.intent == case.expected_intent
        skill_ok = (skill_decision.skill_name or "") == case.expected_skill_name and bool(skill_decision.use_skill) == bool(case.expected_skill_name)
        results.append(
            {
                "case_id": case.case_id,
                "message": case.message,
                "expected_intent": case.expected_intent,
                "actual_intent": decision.intent,
                "expected_skill_name": case.expected_skill_name,
                "actual_skill_name": skill_decision.skill_name,
                "route_correct": route_ok,
                "skill_correct": skill_ok,
                "allowed_tools": list(decision.allowed_tools),
            }
        )

    summary = {
        "total_cases": len(results),
        "route_correctness": round(_safe_rate(sum(1 for item in results if item["route_correct"]), len(results)), 4),
        "skill_decision_correctness": round(_safe_rate(sum(1 for item in results if item["skill_correct"]), len(results)), 4),
        "false_skill_trigger_rate": round(
            _safe_rate(sum(1 for item in results if item["expected_skill_name"] == "" and item["actual_skill_name"] != ""), len(results)),
            4,
        ),
        "missed_skill_rate": round(
            _safe_rate(sum(1 for item in results if item["expected_skill_name"] and item["actual_skill_name"] == ""), len(results)),
            4,
        ),
    }
    return {"summary": summary, "cases": results}


def _run_guard_suite() -> dict[str, Any]:
    agent = AgentManager()
    grader = KnowledgeAnswerGrader(agent)
    cases = [
        GuardCase(
            case_id="supported_case",
            answer="营业收入为90亿元，同比增长12%。",
            retrieval_result=_retrieval_result(
                evidences=[_evidence("knowledge/report.pdf", "page 1", "营业收入为90亿元，同比增长12%。")]
            ),
            expect_guard=False,
        ),
        GuardCase(
            case_id="unsupported_number_case",
            answer="营业收入为100亿元，同比增长12%。",
            retrieval_result=_retrieval_result(
                evidences=[_evidence("knowledge/report.pdf", "page 1", "营业收入为90亿元，同比增长12%。")]
            ),
            expect_guard=True,
            expected_trigger="unsupported_numbers_or_locators",
            counts_numeric=True,
        ),
        GuardCase(
            case_id="unsupported_locator_case",
            answer="根据第9页，营业收入为90亿元。",
            retrieval_result=_retrieval_result(
                evidences=[_evidence("knowledge/report.pdf", "page 1", "营业收入为90亿元，同比增长12%。")]
            ),
            expect_guard=True,
            expected_trigger="unsupported_numbers_or_locators",
            counts_locator=True,
        ),
        GuardCase(
            case_id="unsupported_inference_case",
            answer="公司已经亏损。",
            retrieval_result=_retrieval_result(
                evidences=[_evidence("knowledge/report.pdf", "page 2", "净利润同比下降12%，但仍为正值。")]
            ),
            expect_guard=True,
            expected_trigger="unsupported_inference_terms",
        ),
        GuardCase(
            case_id="directory_guide_case",
            answer="这里给出了一份完整结论。",
            retrieval_result=_retrieval_result(
                status="partial",
                question_type="multi_hop",
                evidences=[_evidence("knowledge/data_structure.md", "section 1", "This file describes the knowledge layout only.")],
            ),
            expect_guard=True,
            expected_trigger="directory_guides_only",
        ),
    ]

    results: list[dict[str, Any]] = []
    numeric_blocked = 0
    locator_blocked = 0
    for case in cases:
        decision = grader.grade(case.answer, case.retrieval_result)
        actual_trigger = decision.guard_result.details["trigger"] if decision.guard_result is not None else ""
        guard_ok = decision.downgraded == case.expect_guard and actual_trigger == case.expected_trigger
        if case.counts_numeric and decision.downgraded:
            numeric_blocked += 1
        if case.counts_locator and decision.downgraded:
            locator_blocked += 1
        results.append(
            {
                "case_id": case.case_id,
                "expect_guard": case.expect_guard,
                "actual_guard": decision.downgraded,
                "expected_trigger": case.expected_trigger,
                "actual_trigger": actual_trigger,
                "guard_correct": guard_ok,
                "final_answer": decision.final_answer,
            }
        )

    numeric_cases = sum(1 for item in cases if item.counts_numeric)
    locator_cases = sum(1 for item in cases if item.counts_locator)
    summary = {
        "total_cases": len(results),
        "guard_case_accuracy": round(_safe_rate(sum(1 for item in results if item["guard_correct"]), len(results)), 4),
        "unsupported_numeric_hallucination_rate": round(1.0 - _safe_rate(numeric_blocked, numeric_cases), 4) if numeric_cases else 0.0,
        "unsupported_locator_hallucination_rate": round(1.0 - _safe_rate(locator_blocked, locator_cases), 4) if locator_cases else 0.0,
        "evidence_support_rate": round(
            _safe_rate(sum(1 for item in results if (not item["expect_guard"] and not item["actual_guard"]) or (item["expect_guard"] and item["actual_guard"])), len(results)),
            4,
        ),
    }
    return {"summary": summary, "cases": results}


async def run_benchmark(output_path: Path) -> dict[str, Any]:
    lifecycle = await _run_lifecycle_suite()
    route_skill = await _run_route_skill_suite()
    guard = _run_guard_suite()

    summary = {
        "total_cases": lifecycle["summary"]["total_cases"] + route_skill["summary"]["total_cases"] + guard["summary"]["total_cases"],
        "route_trace_presence": lifecycle["summary"]["route_trace_presence"],
        "retrieval_trace_presence": lifecycle["summary"]["retrieval_trace_presence"],
        "tool_trace_presence": lifecycle["summary"]["tool_trace_presence"],
        "final_answer_presence": lifecycle["summary"]["final_answer_presence"],
        "completion_integrity": lifecycle["summary"]["completion_integrity"],
        "queue_integrity": lifecycle["summary"]["queue_integrity"],
        "trace_completeness": lifecycle["summary"]["trace_completeness"],
        "tool_result_to_final_answer_reflection": lifecycle["summary"]["tool_result_to_final_answer_reflection"],
        "route_correctness": route_skill["summary"]["route_correctness"],
        "skill_decision_correctness": route_skill["summary"]["skill_decision_correctness"],
        "false_skill_trigger_rate": route_skill["summary"]["false_skill_trigger_rate"],
        "missed_skill_rate": route_skill["summary"]["missed_skill_rate"],
        "guard_case_accuracy": guard["summary"]["guard_case_accuracy"],
        "evidence_support_rate": guard["summary"]["evidence_support_rate"],
        "unsupported_numeric_hallucination_rate": guard["summary"]["unsupported_numeric_hallucination_rate"],
        "unsupported_locator_hallucination_rate": guard["summary"]["unsupported_locator_hallucination_rate"],
    }

    payload = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "suites": {
            "lifecycle": lifecycle,
            "route_skill": route_skill,
            "guard": guard,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    args = _parse_args()
    payload = asyncio.run(run_benchmark(Path(args.output)))
    print(args.output)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
