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

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graph.agent import AgentManager
from harness.graders import KnowledgeAnswerGrader
from harness.policy import SessionSerialQueue
from harness.runtime import HarnessRuntime, RuntimeDependencies
from harness.trace_store import RunTraceStore
from harness.types import AnswerRecord, RetrievalEvidenceRecord, RetrievalRecord, ToolCallRecord


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


class ScenarioExecutor:
    def __init__(self, spec: LifecycleCase, *, delay_seconds: float = 0.0) -> None:
        self.spec = spec
        self.delay_seconds = delay_seconds

    async def execute(self, runtime: HarnessRuntime, handle, *, message: str, history: list[dict[str, Any]]) -> None:
        route_payload = {
            "intent": "direct_answer",
            "needs_tools": False,
            "needs_retrieval": False,
            "allowed_tools": [],
            "confidence": 1.0,
            "reason_short": self.spec.scenario,
            "source": "benchmark",
            "subtype": "",
            "ambiguity_flags": [],
            "escalated": False,
            "model_name": "",
        }
        if self.spec.scenario == "knowledge_qa":
            route_payload["intent"] = "knowledge_qa"
            route_payload["needs_retrieval"] = True
        elif self.spec.scenario == "tool_path":
            route_payload["intent"] = "workspace_file_ops"
            route_payload["needs_tools"] = True
            route_payload["allowed_tools"] = ["terminal"]
            route_payload["subtype"] = "search_workspace_file"
        await runtime.emit(handle, "route.decided", route_payload)
        await runtime.emit(
            handle,
            "skill.decided",
            {"use_skill": False, "skill_name": "", "confidence": 0.1, "reason_short": "benchmark"},
        )

        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)

        if self.spec.expect_retrieval:
            await runtime.emit(
                handle,
                "retrieval.started",
                {"kind": "knowledge", "stage": "knowledge", "title": "Knowledge retrieval", "message": ""},
            )
            await runtime.emit(
                handle,
                "retrieval.completed",
                RetrievalRecord(
                    kind="knowledge",
                    stage="fused",
                    title="Knowledge retrieval",
                    message="benchmark retrieval",
                    results=(
                        RetrievalEvidenceRecord(
                            source_path="knowledge/report.pdf",
                            source_type="pdf",
                            locator="page 1",
                            snippet="benchmark evidence",
                            channel="fused",
                            score=0.9,
                        ),
                    ),
                ).to_dict(),
            )

        if self.spec.expect_tool:
            tool_output = "a.txt\nb.txt"
            await runtime.emit(
                handle,
                "tool.started",
                ToolCallRecord(tool="terminal", input="Get-ChildItem", call_id=f"{self.spec.case_id}-tool").to_dict(),
            )
            await runtime.emit(
                handle,
                "tool.completed",
                ToolCallRecord(
                    tool="terminal",
                    input="Get-ChildItem",
                    output=tool_output,
                    call_id=f"{self.spec.case_id}-tool",
                ).to_dict(),
            )
            runtime.advance_answer_segment(handle)

        await runtime.emit(
            handle,
            "answer.started",
            AnswerRecord(content="", segment_index=runtime.current_segment_index(handle), final=False).to_dict(),
        )
        answer_text = f"{self.spec.case_id} answer"
        if self.spec.expect_tool:
            answer_text = "Found files: a.txt and b.txt."
        await runtime.emit(
            handle,
            "answer.delta",
            AnswerRecord(
                content=answer_text,
                segment_index=runtime.current_segment_index(handle),
                final=False,
            ).to_dict(),
        )
        if self.spec.expect_failure:
            raise RuntimeError(f"{self.spec.case_id} failed")
        await runtime.emit(
            handle,
            "answer.completed",
            AnswerRecord(
                content=answer_text,
                segment_index=runtime.current_segment_index(handle),
                final=True,
                input_tokens=10,
                output_tokens=4,
            ).to_dict(),
        )


class _NoRouterAllowed:
    async def route(self, **_kwargs):
        raise AssertionError("routing benchmark unexpectedly required the LLM router")


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

        async def _run_case(spec: LifecycleCase, *, delay_seconds: float = 0.0) -> str:
            events = [
                event
                async for event in runtime.run_with_executor(
                    user_message=f"{spec.case_id} message",
                    session_id=spec.session_id,
                    source="benchmark",
                    executor=ScenarioExecutor(spec, delay_seconds=delay_seconds),
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

        first_queued_task = asyncio.create_task(
            _run_case(
                LifecycleCase(case_id="queue_holder", scenario="direct_answer", session_id="session-queued"),
                delay_seconds=0.1,
            )
        )
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
