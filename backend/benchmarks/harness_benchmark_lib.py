from __future__ import annotations

import asyncio
import json
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import patch

from benchmarks.local_http_fixture import serve_local_http_routes, substitute_web_base_url
from src.backend.capabilities import build_tools_and_registry
from src.backend.capabilities.registry import build_capability_registry
from src.backend.decision.execution_strategy import ExecutionStrategy
from src.backend.decision.lightweight_router import RoutingDecision
from src.backend.decision.skill_gate import SkillDecision
from src.backend.knowledge.query_rewrite import build_query_plan
from src.backend.knowledge.types import Evidence, OrchestratedRetrievalResult, RetrievalStep
from src.backend.observability.trace_store import RunTraceStore
from src.backend.orchestration.checkpointing import checkpoint_store
from src.backend.runtime.agent_manager import AgentManager
from src.backend.runtime.execution_support import HarnessExecutionSupport
from src.backend.runtime.executors import HarnessExecutors
from src.backend.runtime.graders import HarnessBenchmarkJudge, HarnessLLMJudge, KnowledgeAnswerGrader
from src.backend.runtime.policy import SessionSerialQueue
from src.backend.runtime.runtime import HarnessRuntime, RuntimeDependencies


SuiteName = Literal["contract", "integration", "hard", "rewrite", "scalable", "all"]
RunnerName = Literal["contract_lifecycle", "integration_lifecycle", "route_skill", "guard", "rewrite_planner"]


CASE_DIR = Path(__file__).resolve().parent / "harness_cases"
DEFAULT_CASE_FILES: dict[str, tuple[Path, ...]] = {
    "contract": (CASE_DIR / "contract_cases.json",),
    "integration": (CASE_DIR / "integration_cases.json",),
    "hard": (
        CASE_DIR / "hard_cases.json",
        CASE_DIR / "dirty_evidence_cases.json",
        CASE_DIR / "adversarial_cases.json",
        CASE_DIR / "mixed_execution_cases.json",
    ),
    "rewrite": (CASE_DIR / "rewrite_cases.json",),
    "scalable": (CASE_DIR / "scalable_cases.json",),
}


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    suite: str
    runner: RunnerName
    bucket: str = ""
    difficulty: str = "smoke"
    tags: tuple[str, ...] = ()
    scenario: str = ""
    message: str = ""
    session_id: str = ""
    answer: str = ""
    expect: dict[str, Any] = field(default_factory=dict)
    retrieval_result: dict[str, Any] | None = None
    setup: dict[str, Any] = field(default_factory=dict)
    tool_calls: tuple[dict[str, Any], ...] = ()


def _load_cases_from_file(path: Path) -> list[BenchmarkCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    loaded: list[BenchmarkCase] = []
    for raw in payload.get("cases", []):
        loaded.append(
            BenchmarkCase(
                case_id=str(raw["case_id"]),
                suite=str(raw.get("suite", path.stem.replace("_cases", ""))),
                runner=str(raw["runner"]),  # type: ignore[arg-type]
                bucket=str(raw.get("bucket", path.stem.replace("_cases", ""))),
                difficulty=str(raw.get("difficulty", "smoke")),
                tags=tuple(str(tag) for tag in raw.get("tags", [])),
                scenario=str(raw.get("scenario", "")),
                message=str(raw.get("message", "")),
                session_id=str(raw.get("session_id", "")),
                answer=str(raw.get("answer", "")),
                expect=dict(raw.get("expect", {})),
                retrieval_result=dict(raw["retrieval_result"]) if raw.get("retrieval_result") is not None else None,
                setup=dict(raw.get("setup", {})),
                tool_calls=tuple(dict(item) for item in raw.get("tool_calls", [])),
            )
        )
    return loaded


def resolve_case_files(suite: str, extra_case_files: list[str] | None = None) -> list[Path]:
    paths: list[Path] = []
    if suite == "all":
        for case_paths in DEFAULT_CASE_FILES.values():
            paths.extend(case_paths)
    else:
        paths.extend(DEFAULT_CASE_FILES[suite])
    for item in extra_case_files or []:
        paths.append(Path(item))
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)
    return deduped


def load_cases(
    *,
    suite: str,
    extra_case_files: list[str] | None = None,
    tag: str | None = None,
    limit: int | None = None,
) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for path in resolve_case_files(suite, extra_case_files):
        cases.extend(_load_cases_from_file(path))
    if suite != "all":
        cases = [case for case in cases if case.suite == suite]
    if tag:
        cases = [case for case in cases if tag in case.tags]
    if limit is not None:
        cases = cases[: max(0, int(limit))]
    return cases


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _result_metric_average(results: list[dict[str, Any]], key: str) -> float | None:
    applicable = [item[key] for item in results if item.get(key) is not None]
    if not applicable:
        return None
    true_count = sum(1 for item in applicable if bool(item))
    return _safe_rate(true_count, len(applicable))


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item.get("status") == "passed")
    summary: dict[str, Any] = {
        "total_cases": total,
        "passed_cases": passed,
        "failed_cases": total - passed,
        "route_trace_presence": _result_metric_average(results, "route_trace_present"),
        "retrieval_trace_presence": _result_metric_average(results, "retrieval_trace_present"),
        "tool_trace_presence": _result_metric_average(results, "tool_trace_present"),
        "capability_trace_presence": _result_metric_average(results, "capability_trace_present"),
        "capability_governance_visibility": _result_metric_average(results, "capability_governance_visible"),
        "guard_presence": _result_metric_average(results, "guard_present"),
        "completion_integrity": _result_metric_average(results, "completion_integrity"),
        "queue_integrity": _result_metric_average(results, "queue_integrity"),
        "trace_completeness": _result_metric_average(results, "trace_completeness"),
        "final_answer_presence": _result_metric_average(results, "final_answer_present"),
        "route_correctness": _result_metric_average(results, "route_correct"),
        "skill_decision_correctness": _result_metric_average(results, "skill_correct"),
        "guard_correctness": _result_metric_average(results, "guard_correct"),
        "tool_result_to_final_answer_reflection": _result_metric_average(results, "tool_result_reflected"),
        "judge_pass_rate": _result_metric_average(results, "judge_passed"),
        "llm_judge_pass_rate": _result_metric_average(results, "llm_judge_passed"),
        "judge_disagreement_rate": _result_metric_average(results, "judge_disagreement"),
        "rewrite_preserves_intent_rate": _result_metric_average(results, "rewrite_preserves_intent"),
        "rewrite_drift_detected_rate": _result_metric_average(results, "rewrite_drift_detected"),
        "planner_reasonable_rate": _result_metric_average(results, "planner_reasonable"),
    }

    numeric_cases = [item for item in results if item.get("counts_numeric") is True]
    locator_cases = [item for item in results if item.get("counts_locator") is True]
    numeric_blocked = sum(1 for item in numeric_cases if item.get("actual_guard"))
    locator_blocked = sum(1 for item in locator_cases if item.get("actual_guard"))
    summary["unsupported_numeric_hallucination_rate"] = (
        round(1.0 - (numeric_blocked / len(numeric_cases)), 4) if numeric_cases else None
    )
    summary["unsupported_locator_hallucination_rate"] = (
        round(1.0 - (locator_blocked / len(locator_cases)), 4) if locator_cases else None
    )
    judge_dimensions: dict[str, list[bool]] = {}
    for item in results:
        judge = item.get("judge_result") or {}
        dimensions = judge.get("dimensions") or {}
        if not isinstance(dimensions, dict):
            continue
        for key, value in dimensions.items():
            judge_dimensions.setdefault(str(key), []).append(bool(value))
    if judge_dimensions:
        summary["judge_dimensions"] = {
            key: _safe_rate(sum(1 for value in values if value), len(values))
            for key, values in sorted(judge_dimensions.items())
        }
    llm_dimensions: dict[str, list[bool]] = {}
    for item in results:
        judge = item.get("llm_judge_result") or {}
        dimensions = judge.get("dimensions") or {}
        if not isinstance(dimensions, dict):
            continue
        for key, value in dimensions.items():
            llm_dimensions.setdefault(str(key), []).append(bool(value))
    if llm_dimensions:
        summary["llm_judge_dimensions"] = {
            key: _safe_rate(sum(1 for value in values if value), len(values))
            for key, values in sorted(llm_dimensions.items())
        }
    bucket_groups: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        bucket = str(item.get("bucket", "") or "").strip()
        if bucket:
            bucket_groups.setdefault(bucket, []).append(item)
    if bucket_groups:
        summary["buckets"] = {
            bucket: {
                "total_cases": len(items),
                "passed_cases": sum(1 for item in items if item.get("status") == "passed"),
                "judge_pass_rate": _result_metric_average(items, "judge_passed"),
                "llm_judge_pass_rate": _result_metric_average(items, "llm_judge_passed"),
            }
            for bucket, items in sorted(bucket_groups.items())
        }
    return summary


def _trace_event_names(trace: dict[str, Any]) -> list[str]:
    return [str(item.get("name", "")) for item in trace.get("events", [])]


def _merge_traces(*traces: dict[str, Any]) -> dict[str, Any]:
    materialized = [trace for trace in traces if trace]
    if not materialized:
        raise ValueError("at least one trace is required")
    base = dict(materialized[-1])
    base["events"] = [
        dict(event)
        for trace in materialized
        for event in list(trace.get("events", []) or [])
    ]
    first_metadata = dict(materialized[0].get("metadata", {}) or {})
    last_metadata = dict(materialized[-1].get("metadata", {}) or {})
    merged_metadata = dict(first_metadata)
    merged_metadata.update(
        {
            "run_id": str(last_metadata.get("run_id", "") or first_metadata.get("run_id", "") or ""),
            "source": str(last_metadata.get("source", "") or first_metadata.get("source", "") or ""),
            "checkpoint_id": str(last_metadata.get("checkpoint_id", "") or first_metadata.get("checkpoint_id", "") or ""),
            "resume_source": str(last_metadata.get("resume_source", "") or first_metadata.get("resume_source", "") or ""),
        }
    )
    base["metadata"] = merged_metadata
    merged_outcome = dict(materialized[0].get("outcome", {}) or {})
    for trace in materialized[1:]:
        current_outcome = dict(trace.get("outcome", {}) or {})
        for key, value in current_outcome.items():
            if value not in ("", None, [], {}, ()):
                merged_outcome[key] = value
    base["outcome"] = merged_outcome
    return base


class _NoRouterAllowed:
    async def route(self, **_kwargs):
        raise AssertionError("benchmark unexpectedly required the LLM router")


class _ScenarioRouter:
    def __init__(self, case_specs: dict[str, BenchmarkCase]) -> None:
        self._case_specs = case_specs

    async def route(self, *, message: str, **_kwargs):
        spec = self._case_specs[str(message or "").strip()]
        payload = dict(spec.expect.get("llm_decision", {}) or {})
        if not payload:
            raise AssertionError(f"benchmark case {spec.case_id} expected no LLM routing fallback")
        return RoutingDecision(
            intent=str(payload.get("intent", spec.expect.get("route_intent", "direct_answer"))),
            needs_tools=bool(payload.get("needs_tools", False)),
            needs_retrieval=bool(payload.get("needs_retrieval", False)),
            allowed_tools=tuple(str(item) for item in payload.get("allowed_tools", []) or []),
            confidence=float(payload.get("confidence", 0.7) or 0.7),
            reason_short=str(payload.get("reason_short", "benchmark router")),
            source="benchmark_llm_router",
            ambiguity_flags=tuple(str(item) for item in payload.get("ambiguity_flags", []) or []),
            escalated=bool(payload.get("escalated", False)),
            model_name=str(payload.get("model_name", "benchmark-router")),
            subtype=str(payload.get("subtype", "") or ""),
        )


class _BenchmarkToolMessage:
    def __init__(
        self,
        *,
        message_type: str,
        content: str = "",
        tool_calls=None,
        tool_call_id: str = "",
        name: str = "",
    ) -> None:
        self.type = message_type
        self.content = content
        self.tool_calls = list(tool_calls or [])
        self.tool_call_id = tool_call_id
        self.name = name


class _BenchmarkToolAgent:
    def __init__(
        self,
        case_id: str,
        tool_outputs: list[str] | None = None,
        *,
        tool_calls: list[dict[str, Any]] | None = None,
        tools_by_name: dict[str, Any] | None = None,
    ) -> None:
        self._case_id = case_id
        self._tool_outputs = list(tool_outputs or ["a.txt\nb.txt"])
        self._tool_calls = [dict(item) for item in (tool_calls or [])]
        self._tools_by_name = dict(tools_by_name or {})

    async def astream(self, _inputs, stream_mode=None):
        if self._tool_calls:
            for index, tool_call in enumerate(self._tool_calls, start=1):
                call_id = f"{self._case_id}-tool-{index}"
                tool_name = str(tool_call.get("name", "terminal") or "terminal")
                tool_args = dict(tool_call.get("args", {}) or {})
                yield (
                    "updates",
                    {
                        "tool_node": {
                            "messages": [
                                _BenchmarkToolMessage(
                                    message_type="ai",
                                    tool_calls=[{"id": call_id, "name": tool_name, "args": tool_args}],
                                )
                            ]
                        }
                    },
                )
                tool = self._tools_by_name[tool_name]
                output = await tool.ainvoke(tool_args)
                yield (
                    "updates",
                    {
                        "tool_node": {
                            "messages": [
                                _BenchmarkToolMessage(
                                    message_type="tool",
                                    content=str(output or ""),
                                    tool_call_id=call_id,
                                    name=tool_name,
                                )
                            ]
                        }
                    },
                )
            return
        for index, output in enumerate(self._tool_outputs, start=1):
            call_id = f"{self._case_id}-tool-{index}"
            yield (
                "updates",
                {
                    "tool_node": {
                        "messages": [
                            _BenchmarkToolMessage(
                                message_type="ai",
                                tool_calls=[{"id": call_id, "name": "terminal", "args": {"command": f"Get-ChildItem #{index}"}}],
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
                                content=output,
                                tool_call_id=call_id,
                                name="terminal",
                            )
                        ]
                    }
                },
            )


class _ContractBenchmarkExecutionSupport(HarnessExecutionSupport):
    def __init__(self, agent_manager: "ContractBenchmarkAgentManager") -> None:
        super().__init__(agent_manager)
        self._agent_manager = agent_manager

    async def astream_model_answer(self, messages: list[dict[str, str]], extra_instructions=None, system_prompt_override=None):
        spec = self._agent_manager._spec_for_message(messages[-1]["content"])
        if spec.expect.get("failure"):
            raise RuntimeError(f"{spec.case_id} failed")
        final_text = str(spec.expect.get("answer_fragment", "") or f"{spec.case_id} answer")
        if spec.expect.get("queue") or spec.case_id.endswith("_holder"):
            await asyncio.sleep(0.1)
        midpoint = max(1, len(final_text) // 2)
        yield {"type": "token", "content": final_text[:midpoint]}
        yield {"type": "token", "content": final_text[midpoint:]}
        yield {"type": "done", "content": final_text, "usage": {"input_tokens": 10, "output_tokens": 4}}

    def build_tool_agent(self, *, extra_instructions=None, tools_override=None):
        return _BenchmarkToolAgent(
            "contract_tool",
            tool_outputs=list(
                self._agent_manager._spec_for_message("contract_tool message").expect.get("tool_outputs", ["a.txt\nb.txt"])
            ),
        )


class ContractBenchmarkAgentManager(AgentManager):
    """Controlled provider for fast contract regressions, still executed through real HarnessExecutors."""

    def __init__(self, case_specs: dict[str, BenchmarkCase]) -> None:
        super().__init__()
        self._case_specs = case_specs
        self.base_dir = Path(__file__).resolve().parents[1]
        self.tools = [SimpleNamespace(name="terminal"), SimpleNamespace(name="fetch_url")]
        self._capability_registry = build_capability_registry(self.tools)
        self._lightweight_router = _NoRouterAllowed()

    def _spec_for_message(self, message: str) -> BenchmarkCase:
        return self._case_specs[str(message or "").strip()]

    async def resolve_routing(self, message: str, history: list[dict[str, Any]]) -> tuple[ExecutionStrategy, RoutingDecision]:
        spec = self._spec_for_message(message)
        if spec.scenario == "knowledge_qa":
            return (
                ExecutionStrategy(allow_tools=False, allow_knowledge=True, allow_retrieval=True),
                RoutingDecision(
                    intent="knowledge_qa",
                    needs_tools=False,
                    needs_retrieval=True,
                    allowed_tools=(),
                    confidence=1.0,
                    reason_short=spec.scenario,
                    source="benchmark",
                    subtype="",
                ),
            )
        if spec.scenario == "tool_path":
            return (
                ExecutionStrategy(allow_tools=True, allow_knowledge=False, allow_retrieval=False),
                RoutingDecision(
                    intent="workspace_file_ops",
                    needs_tools=True,
                    needs_retrieval=False,
                    allowed_tools=("terminal",),
                    confidence=1.0,
                    reason_short=spec.scenario,
                    source="benchmark",
                    subtype="search_workspace_file",
                ),
            )
        return (
            ExecutionStrategy(allow_tools=False, allow_knowledge=False, allow_retrieval=False, force_direct_answer=True),
            RoutingDecision(
                intent="direct_answer",
                needs_tools=False,
                needs_retrieval=False,
                allowed_tools=(),
                confidence=1.0,
                reason_short=spec.scenario,
                source="benchmark",
                subtype="",
            ),
        )

    def decide_skill(
        self,
        message: str,
        history: list[dict[str, Any]],
        strategy: ExecutionStrategy,
        routing_decision: RoutingDecision,
    ) -> SkillDecision:
        return SkillDecision(False, "", 0.0, "contract benchmark disables skills")

    def _runtime_rag_mode(self) -> bool:
        return False

    def _knowledge_system_prompt(self) -> str:
        return "Contract benchmark knowledge prompt"

    def create_execution_support(self) -> HarnessExecutionSupport:
        return _ContractBenchmarkExecutionSupport(self)

    def _resolve_tools_for_strategy(self, strategy: ExecutionStrategy) -> list[Any]:
        if not strategy.allow_tools:
            return []
        return [tool for tool in self.tools if getattr(tool, "name", "") == "terminal"]

    def _build_knowledge_scaffold(self, message: str, retrieval_result) -> str:
        return ""

    def _knowledge_answer_instructions(self, retrieval_result) -> list[str]:
        return ["Use the evidence only."]


class IntegrationBenchmarkSupport(HarnessExecutionSupport):
    """Minimal controlled doubles around a real AgentManager capability path."""

    def __init__(self, agent_manager: AgentManager, case_specs: dict[str, BenchmarkCase]) -> None:
        super().__init__(agent_manager)
        self.case_specs = case_specs
        self.active_case: BenchmarkCase | None = None

    def spec_for_message(self, message: str) -> BenchmarkCase:
        normalized = str(message or "").strip()
        for case in self.case_specs.values():
            if case.message == normalized:
                return case
        raise KeyError(f"unknown benchmark case message: {message}")

    def set_active_case(self, case: BenchmarkCase) -> None:
        self.active_case = case

    async def astream_model_answer(self, messages: list[dict[str, str]], extra_instructions=None, system_prompt_override=None):
        spec = self.spec_for_message(messages[-1]["content"])
        if spec.expect.get("failure"):
            raise RuntimeError(f"{spec.case_id} failed")
        if spec.expect.get("model_answer"):
            final_text = str(spec.expect.get("model_answer"))
        elif extra_instructions and any("tool calls already succeeded" in item.lower() for item in extra_instructions):
            final_text = str(spec.expect.get("answer_fragment", "") or spec.case_id)
        elif spec.scenario == "knowledge_qa":
            final_text = "knowledge_case answer"
        elif spec.scenario == "guarded_knowledge":
            final_text = "Revenue was 100 billion based on page 7."
        elif spec.case_id == "integration_queue_holder":
            final_text = "Queue holder answer."
        else:
            final_text = str(spec.expect.get("answer_fragment", "") or spec.case_id)
        if spec.case_id == "integration_queue_holder":
            await asyncio.sleep(0.1)
        midpoint = max(1, len(final_text) // 2)
        yield {"type": "token", "content": final_text[:midpoint]}
        yield {"type": "token", "content": final_text[midpoint:]}
        yield {"type": "done", "content": final_text, "usage": {"input_tokens": 10, "output_tokens": 4}}

    async def knowledge_astream(self, message: str):
        spec = self.spec_for_message(message)
        if spec.retrieval_result is not None:
            yield {"type": "orchestrated_result", "result": _dict_to_retrieval_result(spec.retrieval_result)}
            return
        snippet = "knowledge_case answer"
        question_type = "direct_fact"
        if spec.scenario == "guarded_knowledge":
            snippet = "Revenue was 10 billion."
            question_type = "compare"
        evidence = Evidence(
            source_path="knowledge/report.pdf",
            source_type="pdf",
            locator="page 1",
            snippet=snippet,
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
            question_type=question_type,
            entity_hints=["benchmark"],
        )
        yield {"type": "orchestrated_result", "result": result}

    def build_tool_agent(self, *, extra_instructions=None, tools_override=None):
        spec = self.active_case
        tool_outputs = list((spec.expect.get("tool_outputs", ["a.txt\nb.txt"]) if spec is not None else ["a.txt\nb.txt"]))
        case_id = spec.case_id if spec is not None else "integration_tool"
        tools = list(tools_override or self._agent.tools)
        return _BenchmarkToolAgent(
            case_id,
            tool_outputs=tool_outputs,
            tool_calls=list(spec.tool_calls if spec is not None else ()),
            tools_by_name={str(getattr(tool, "name", "") or ""): tool for tool in tools},
        )


def _materialize_case_setup(root: Path, setup: dict[str, Any]) -> None:
    for raw_directory in setup.get("directories", []) or []:
        directory_path = root / str(raw_directory.get("path", "") or "")
        directory_path.mkdir(parents=True, exist_ok=True)

    for raw_file in setup.get("files", []) or []:
        file_path = root / str(raw_file.get("path", "") or "")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(str(raw_file.get("content", "") or ""), encoding="utf-8")


def _collect_http_routes(cases: list[BenchmarkCase]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for case in cases:
        for route in case.setup.get("http_routes", []) or []:
            routes.append(dict(route))
    return routes


def _with_web_base_url(case: BenchmarkCase, base_url: str) -> BenchmarkCase:
    return replace(
        case,
        message=str(substitute_web_base_url(case.message, base_url)),
        answer=str(substitute_web_base_url(case.answer, base_url)),
        expect=dict(substitute_web_base_url(case.expect, base_url)),
        retrieval_result=dict(substitute_web_base_url(case.retrieval_result, base_url)) if case.retrieval_result is not None else None,
        setup=dict(substitute_web_base_url(case.setup, base_url)),
        tool_calls=tuple(dict(item) for item in substitute_web_base_url(list(case.tool_calls), base_url)),
    )


def _lifecycle_case_result(case: BenchmarkCase, trace: dict[str, Any], elapsed_ms: int) -> dict[str, Any]:
    event_names = _trace_event_names(trace)
    outcome = trace.get("outcome") or {}
    final_answer = str(outcome.get("final_answer", "") or "")
    expect = case.expect
    route_present = "route.decided" in event_names
    retrieval_present = "retrieval.started" in event_names and "retrieval.completed" in event_names
    tool_present = "tool.started" in event_names and "tool.completed" in event_names
    capability_governance_visible = any(
        name in event_names for name in ("capability.retry", "capability.blocked", "capability.failed")
    ) or any(
        str(item.get("name", "")) == "run.completed" and "capability_governance" in dict(item.get("payload", {}))
        for item in trace.get("events", [])
    )
    capability_present = (
        "capability.started" in event_names and (
            "capability.completed" in event_names
            or "capability.failed" in event_names
            or "capability.blocked" in event_names
        )
    ) or capability_governance_visible
    guard_present = "guard.failed" in event_names
    final_answer_present = "answer.completed" in event_names and bool(final_answer.strip())
    completion_integrity = (
        ("run.failed" in event_names and outcome.get("status") == "failed")
        if expect.get("failure")
        else ("run.completed" in event_names and outcome.get("status") == "completed")
    )
    queue_integrity = (("run.queued" in event_names and "run.dequeued" in event_names) if expect.get("queue") else True)
    reflection_terms = [str(item) for item in (expect.get("judge", {}) or {}).get("reflection_terms", []) or [] if str(item).strip()]
    answer_fragment = str(expect.get("answer_fragment", "") or "")
    if reflection_terms:
        tool_reflection = all(term in final_answer for term in reflection_terms)
    elif answer_fragment:
        tool_reflection = answer_fragment in final_answer
    else:
        tool_reflection = True
    route_correct = True if not expect.get("route_intent") else outcome.get("route_intent") == expect.get("route_intent")
    skill_correct = True if "skill_name" not in expect else (outcome.get("used_skill", "") or "") == str(expect.get("skill_name", ""))
    guard_correct = True if "guard" not in expect else guard_present == bool(expect.get("guard"))
    trace_completeness = route_present and completion_integrity and queue_integrity and tool_reflection and route_correct and skill_correct and guard_correct
    if not expect.get("failure"):
        trace_completeness = trace_completeness and final_answer_present
    if expect.get("retrieval"):
        trace_completeness = trace_completeness and retrieval_present
    if expect.get("tool"):
        trace_completeness = trace_completeness and tool_present

    missing: list[str] = []
    if not route_present:
        missing.append("route_trace_missing")
    if expect.get("retrieval") and not retrieval_present:
        missing.append("retrieval_trace_missing")
    if expect.get("tool") and not tool_present:
        missing.append("tool_trace_missing")
    if not completion_integrity:
        missing.append("completion_integrity_failed")
    if not queue_integrity:
        missing.append("queue_integrity_failed")
    if not tool_reflection:
        missing.append("answer_reflection_failed")
    if not route_correct:
        missing.append("route_incorrect")
    if not skill_correct:
        missing.append("skill_incorrect")
    if not guard_correct:
        missing.append("guard_incorrect")

    return {
        "case_id": case.case_id,
        "suite": case.suite,
        "runner": case.runner,
        "scenario": case.scenario,
        "tags": list(case.tags),
        "difficulty": case.difficulty,
        "run_id": trace["metadata"]["run_id"],
        "status": "passed" if trace_completeness else "failed",
        "failure_reason": ",".join(missing),
        "route_trace_present": route_present,
        "retrieval_trace_present": retrieval_present if expect.get("retrieval") else None,
        "tool_trace_present": tool_present if expect.get("tool") else None,
        "capability_trace_present": capability_present if (capability_present or expect.get("tool") or expect.get("skill_name") or case.scenario.startswith("hitl_")) else None,
        "capability_governance_visible": capability_governance_visible if (capability_governance_visible or expect.get("tool") or expect.get("skill_name") or case.scenario.startswith("hitl_")) else None,
        "guard_present": guard_present if "guard" in expect else None,
        "completion_integrity": completion_integrity,
        "queue_integrity": queue_integrity if expect.get("queue") else None,
        "final_answer_present": final_answer_present if not expect.get("failure") else None,
        "tool_result_reflected": tool_reflection if (answer_fragment or reflection_terms) else None,
        "route_correct": route_correct if "route_intent" in expect else None,
        "skill_correct": skill_correct if "skill_name" in expect else None,
        "guard_correct": guard_correct if "guard" in expect else None,
        "trace_completeness": trace_completeness,
        "latency_ms": elapsed_ms,
        "event_names": event_names,
        "outcome": outcome,
        "counts_numeric": None,
        "counts_locator": None,
        "actual_guard": None,
        "judge_passed": None,
        "judge_result": None,
    }


async def _fake_contract_knowledge_astream(message: str, case_specs: dict[str, BenchmarkCase]):
    case = case_specs[str(message or "").strip()]
    snippet = str(case.expect.get("answer_fragment", "") or f"{case.case_id} answer")
    evidence = Evidence(
        source_path="knowledge/report.pdf",
        source_type="pdf",
        locator="page 1",
        snippet=snippet,
        channel="fused",
        score=0.9,
    )
    step = RetrievalStep(
        kind="knowledge",
        stage="fused",
        title="Knowledge retrieval",
        message="contract retrieval",
        results=[evidence],
    )
    result = OrchestratedRetrievalResult(
        status="success",
        evidences=[evidence],
        steps=[step],
        reason="contract retrieval",
        question_type="direct_fact",
        entity_hints=["contract"],
    )
    yield {"type": "orchestrated_result", "result": result}


async def _run_case_through_runtime(
    *,
    runtime: HarnessRuntime,
    executor: HarnessExecutors,
    case: BenchmarkCase,
    history: list[dict[str, Any]] | None = None,
    suppress_failures: bool = True,
) -> tuple[str, int]:
    started = time.perf_counter()
    events = [
        event
        async for event in runtime.run_with_executor(
            user_message=case.message,
            session_id=case.session_id or None,
            source="benchmark",
            executor=executor,
            history=list(history or []),
            suppress_failures=suppress_failures,
        )
    ]
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if not events:
        raise RuntimeError(f"no events produced for case {case.case_id}")
    return events[0].run_id, elapsed_ms


async def _run_contract_lifecycle_cases(cases: list[BenchmarkCase]) -> list[dict[str, Any]]:
    case_specs = {case.message: case for case in cases}
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        runtime = HarnessRuntime(
            RuntimeDependencies(
                trace_store=RunTraceStore(root / "runs"),
                queue=SessionSerialQueue(lambda: datetime.now(timezone.utc).isoformat()),
            )
        )
        manager = ContractBenchmarkAgentManager(case_specs)
        executor = HarnessExecutors(manager)
        results: list[dict[str, Any]] = []

        queue_holders: dict[str, BenchmarkCase] = {}
        for case in cases:
            if case.expect.get("queue"):
                holder = BenchmarkCase(
                    case_id=f"{case.case_id}_holder",
                    suite=case.suite,
                    runner=case.runner,
                    scenario="direct_answer",
                    message=f"{case.case_id}_holder message",
                    session_id=case.session_id,
                    tags=("queue", "holder"),
                    difficulty=case.difficulty,
                    expect={"answer_fragment": f"{case.case_id}_holder answer"},
                )
                queue_holders[case.case_id] = holder
                case_specs[holder.message] = holder

        async def _run_one(case: BenchmarkCase) -> tuple[str, int]:
            with patch("src.backend.runtime.executors.knowledge_orchestrator.astream", side_effect=lambda message: _fake_contract_knowledge_astream(message, case_specs)):
                return await _run_case_through_runtime(runtime=runtime, executor=executor, case=case)

        for case in cases:
            if case.expect.get("queue"):
                holder = queue_holders[case.case_id]
                holder_task = asyncio.create_task(_run_one(holder))
                await asyncio.sleep(0.02)
                run_id, elapsed_ms = await _run_one(case)
                await holder_task
            else:
                run_id, elapsed_ms = await _run_one(case)
            trace = runtime._deps.trace_store.read_trace(run_id)  # noqa: SLF001
            results.append(_lifecycle_case_result(case, trace, elapsed_ms))
        return results


async def _run_integration_lifecycle_cases(
    cases: list[BenchmarkCase],
    *,
    use_live_llm_decisions: bool = True,
) -> list[dict[str, Any]]:
    support: IntegrationBenchmarkSupport | None = None
    with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
        root = Path(temp_dir)
        for case in cases:
            _materialize_case_setup(root, case.setup)
        http_routes = _collect_http_routes(cases)
        if http_routes:
            base_url = stack.enter_context(serve_local_http_routes(http_routes))
            cases = [_with_web_base_url(case, base_url) for case in cases]
        case_specs = {case.message: case for case in cases}
        runtime = HarnessRuntime(
            RuntimeDependencies(
                trace_store=RunTraceStore(root / "runs"),
                queue=SessionSerialQueue(lambda: datetime.now(timezone.utc).isoformat()),
            )
        )
        manager = AgentManager()
        manager.base_dir = root
        manager.tools, manager._capability_registry = build_tools_and_registry(root)  # noqa: SLF001
        support = IntegrationBenchmarkSupport(manager, case_specs)
        manager.create_execution_support = lambda: support  # type: ignore[method-assign]
        if not use_live_llm_decisions:
            async def _resolve_case_routing(message: str, history: list[dict[str, Any]]):
                spec = support.spec_for_message(message)
                if spec.scenario in {"knowledge_qa", "guarded_knowledge"}:
                    return (
                        ExecutionStrategy(allow_tools=False, allow_knowledge=True, allow_retrieval=True),
                        RoutingDecision(
                            intent="knowledge_qa",
                            needs_tools=False,
                            needs_retrieval=True,
                            allowed_tools=(),
                            confidence=1.0,
                            reason_short=spec.scenario,
                            source="benchmark",
                            subtype="",
                        ),
                    )
                if spec.scenario == "tool_path":
                    return (
                        ExecutionStrategy(allow_tools=True, allow_knowledge=False, allow_retrieval=False),
                        RoutingDecision(
                            intent="workspace_file_ops",
                            needs_tools=True,
                            needs_retrieval=False,
                            allowed_tools=("terminal",),
                            confidence=1.0,
                            reason_short=spec.scenario,
                            source="benchmark",
                            subtype="search_workspace_file",
                        ),
                    )
                if spec.scenario.startswith("hitl_"):
                    allowed_tool = tuple(
                        str(item.get("name", "") or "")
                        for item in spec.tool_calls[:1]
                        if str(item.get("name", "") or "").strip()
                    ) or ("python_repl",)
                    return (
                        ExecutionStrategy(allow_tools=True, allow_knowledge=False, allow_retrieval=False),
                        RoutingDecision(
                            intent="workspace_file_ops",
                            needs_tools=True,
                            needs_retrieval=False,
                            allowed_tools=allowed_tool,
                            confidence=1.0,
                            reason_short=spec.scenario,
                            source="benchmark",
                            subtype="",
                        ),
                    )
                if spec.scenario.startswith("mcp_filesystem_"):
                    allowed_tool = tuple(
                        str(item.get("name", "") or "")
                        for item in spec.tool_calls[:1]
                        if str(item.get("name", "") or "").strip()
                    ) or ("mcp_filesystem_read_file",)
                    subtype = "read_existing_file" if allowed_tool[0] == "mcp_filesystem_read_file" else "search_workspace_file"
                    return (
                        ExecutionStrategy(allow_tools=True, allow_knowledge=False, allow_retrieval=False),
                        RoutingDecision(
                            intent="workspace_file_ops",
                            needs_tools=True,
                            needs_retrieval=False,
                            allowed_tools=allowed_tool,
                            confidence=1.0,
                            reason_short=spec.scenario,
                            source="benchmark",
                            subtype=subtype,
                        ),
                    )
                if spec.scenario.startswith("mcp_web_"):
                    allowed_tool = tuple(
                        str(item.get("name", "") or "")
                        for item in spec.tool_calls[:1]
                        if str(item.get("name", "") or "").strip()
                    ) or ("mcp_web_fetch_url",)
                    return (
                        ExecutionStrategy(allow_tools=True, allow_knowledge=False, allow_retrieval=False),
                        RoutingDecision(
                            intent="web_lookup",
                            needs_tools=True,
                            needs_retrieval=False,
                            allowed_tools=allowed_tool,
                            confidence=1.0,
                            reason_short=spec.scenario,
                            source="benchmark",
                            subtype="",
                        ),
                    )
                return (
                    ExecutionStrategy(allow_tools=False, allow_knowledge=False, allow_retrieval=False, force_direct_answer=True),
                    RoutingDecision(
                        intent="direct_answer",
                        needs_tools=False,
                        needs_retrieval=False,
                        allowed_tools=(),
                        confidence=1.0,
                        reason_short=spec.scenario,
                        source="benchmark",
                        subtype="",
                    ),
                )

            manager.resolve_routing = _resolve_case_routing  # type: ignore[method-assign]
        executor = HarnessExecutors(manager)
        results: list[dict[str, Any]] = []

        queue_holders: dict[str, BenchmarkCase] = {}
        for case in cases:
            if case.expect.get("queue"):
                holder = BenchmarkCase(
                    case_id="integration_queue_holder",
                    suite=case.suite,
                    runner=case.runner,
                    scenario="direct_answer",
                    message=f"{case.case_id} queue holder message",
                    session_id=case.session_id,
                    tags=("queue", "holder"),
                    difficulty=case.difficulty,
                    expect={"route_intent": "direct_answer", "answer_fragment": "Queue holder answer."},
                )
                queue_holders[case.case_id] = holder
                case_specs[holder.message] = holder

        async def _run_one(case: BenchmarkCase) -> tuple[str, int]:
            support.set_active_case(case)
            with patch("src.backend.runtime.executors.memory_indexer.retrieve", return_value=[]), patch(
                "src.backend.runtime.executors.knowledge_orchestrator.astream",
                side_effect=support.knowledge_astream,
            ):
                return await _run_case_through_runtime(runtime=runtime, executor=executor, case=case)

        for case in cases:
            if case.scenario.startswith("hitl_"):
                run_id, elapsed_ms = await _run_one(case)
                initial_trace = runtime._deps.trace_store.read_trace(run_id)  # noqa: SLF001
                thread_candidates = [
                    str(case.session_id or ""),
                    str((initial_trace.get("metadata") or {}).get("thread_id", "") or ""),
                    str((initial_trace.get("outcome") or {}).get("thread_id", "") or ""),
                    str(run_id),
                ]
                pending = None
                pending_thread_id = ""
                for candidate in thread_candidates:
                    if not candidate:
                        continue
                    pending = checkpoint_store.pending_hitl(thread_id=candidate)
                    if pending is not None:
                        pending_thread_id = candidate
                        break
                checkpoint_id = ""
                if pending is not None:
                    checkpoint_id = pending.checkpoint_id
                else:
                    initial_event_names = _trace_event_names(initial_trace)
                    if "hitl.requested" not in initial_event_names:
                        raise AssertionError(f"no pending HITL interrupt produced for case {case.case_id}")
                    for candidate in thread_candidates:
                        if not candidate:
                            continue
                        latest_checkpoint = checkpoint_store.latest_checkpoint(thread_id=candidate)
                        if latest_checkpoint is not None and latest_checkpoint.resume_eligible:
                            pending_thread_id = candidate
                            checkpoint_id = latest_checkpoint.checkpoint_id
                            break
                    if not checkpoint_id:
                        raise AssertionError(f"no resumable HITL checkpoint produced for case {case.case_id}")
                decision = "reject" if "reject" in case.scenario else "approve"
                resume_executor = HarnessExecutors(
                    manager,
                    resume_checkpoint_id=checkpoint_id,
                    resume_thread_id=pending_thread_id or case.session_id or run_id,
                    resume_source="benchmark_hitl",
                    resume_payload={"decision": decision},
                )
                resumed_run_id, resumed_elapsed_ms = await _run_case_through_runtime(
                    runtime=runtime,
                    executor=resume_executor,
                    case=case,
                    suppress_failures=True,
                )
                resumed_trace = runtime._deps.trace_store.read_trace(resumed_run_id)  # noqa: SLF001
                merged_trace = _merge_traces(initial_trace, resumed_trace)
                results.append(_lifecycle_case_result(case, merged_trace, elapsed_ms + resumed_elapsed_ms))
                checkpoint_store.clear_pending_hitl(thread_id=pending_thread_id or case.session_id or run_id)
                continue
            if case.expect.get("queue"):
                holder = queue_holders[case.case_id]
                holder_task = asyncio.create_task(_run_one(holder))
                await asyncio.sleep(0.02)
                run_id, elapsed_ms = await _run_one(case)
                await holder_task
            else:
                run_id, elapsed_ms = await _run_one(case)
            trace = runtime._deps.trace_store.read_trace(run_id)  # noqa: SLF001
            results.append(_lifecycle_case_result(case, trace, elapsed_ms))
        return results


async def _run_route_skill_cases(
    cases: list[BenchmarkCase],
    *,
    use_live_llm_decisions: bool = True,
) -> list[dict[str, Any]]:
    manager = AgentManager()
    manager.tools = [
        SimpleNamespace(name="fetch_url"),
        SimpleNamespace(name="read_file"),
        SimpleNamespace(name="terminal"),
        SimpleNamespace(name="python_repl"),
        SimpleNamespace(name="mcp_filesystem_read_file"),
        SimpleNamespace(name="mcp_filesystem_list_directory"),
        SimpleNamespace(name="mcp_web_fetch_url"),
    ]
    manager._capability_registry = build_capability_registry(manager.tools)  # noqa: SLF001
    if not use_live_llm_decisions:
        manager._lightweight_router = _ScenarioRouter({case.message: case for case in cases})
    results: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        strategy, decision = await manager.resolve_routing(case.message, [])
        skill_decision = manager.decide_skill(case.message, [], strategy, decision)
        route_ok = decision.intent == str(case.expect.get("route_intent", "") or "")
        skill_ok = (skill_decision.skill_name or "") == str(case.expect.get("skill_name", "") or "") and bool(skill_decision.use_skill) == bool(case.expect.get("skill_name"))
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        results.append(
            {
                "case_id": case.case_id,
                "suite": case.suite,
                "runner": case.runner,
                "scenario": case.scenario,
                "tags": list(case.tags),
                "difficulty": case.difficulty,
                "status": "passed" if route_ok and skill_ok else "failed",
                "failure_reason": "" if route_ok and skill_ok else "route_or_skill_mismatch",
                "route_trace_present": None,
                "retrieval_trace_present": None,
                "tool_trace_present": None,
                "guard_present": None,
                "completion_integrity": True,
                "queue_integrity": None,
                "final_answer_present": None,
                "tool_result_reflected": None,
                "route_correct": route_ok,
                "skill_correct": skill_ok,
                "guard_correct": None,
                "trace_completeness": route_ok and skill_ok,
                "latency_ms": elapsed_ms,
                "event_names": [],
                "outcome": {
                    "route_intent": decision.intent,
                    "used_skill": skill_decision.skill_name,
                    "allowed_tools": list(decision.allowed_tools),
                    "route_source": decision.source,
                    "route_confidence": decision.confidence,
                    "route_reason_short": decision.reason_short,
                    "route_subtype": decision.subtype,
                    "route_ambiguity_flags": list(decision.ambiguity_flags),
                    "skill_confidence": skill_decision.confidence,
                    "skill_reason_short": skill_decision.reason_short,
                },
                "counts_numeric": None,
                "counts_locator": None,
                "actual_guard": None,
                "judge_passed": None,
                "judge_result": None,
            }
        )
    return results


def _dict_to_retrieval_result(payload: dict[str, Any]) -> OrchestratedRetrievalResult:
    evidences = [
        Evidence(
            source_path=str(item.get("source_path", "") or ""),
            source_type=str(item.get("source_type", "pdf") or "pdf"),
            locator=str(item.get("locator", "") or ""),
            snippet=str(item.get("snippet", "") or ""),
            channel=str(item.get("channel", "fused") or "fused"),  # type: ignore[arg-type]
            score=float(item.get("score")) if item.get("score") is not None else None,
        )
        for item in payload.get("evidences", []) or []
    ]
    steps_payload = payload.get("steps", None)
    if steps_payload:
        steps = [
            RetrievalStep(
                kind=str(item.get("kind", "knowledge") or "knowledge"),
                stage=str(item.get("stage", "fused") or "fused"),
                title=str(item.get("title", "Knowledge retrieval") or "Knowledge retrieval"),
                message=str(item.get("message", "") or ""),
                results=evidences,
            )
            for item in steps_payload
        ]
    else:
        steps = [
            RetrievalStep(
                kind="knowledge",
                stage="fused",
                title="Knowledge retrieval",
                message=str(payload.get("reason", "") or "benchmark retrieval"),
                results=evidences,
            )
        ] if evidences else []
    return OrchestratedRetrievalResult(
        status=str(payload.get("status", "success") or "success"),
        evidences=evidences,
        steps=steps,
        fallback_used=bool(payload.get("fallback_used", False)),
        reason=str(payload.get("reason", "") or ""),
        question_type=str(payload.get("question_type", "direct_fact") or "direct_fact"),
        entity_hints=[str(item) for item in payload.get("entity_hints", []) or []],
    )


def _run_guard_cases(cases: list[BenchmarkCase]) -> list[dict[str, Any]]:
    grader = KnowledgeAnswerGrader(AgentManager())
    results: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        retrieval_result = _dict_to_retrieval_result(case.retrieval_result or {})
        decision = grader.grade(case.answer, retrieval_result)
        actual_trigger = decision.guard_result.details.get("trigger", "") if decision.guard_result is not None else ""
        expect_guard = bool(case.expect.get("guard"))
        expected_trigger = str(case.expect.get("trigger", "") or "")
        guard_ok = decision.downgraded == expect_guard and actual_trigger == expected_trigger
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        results.append(
            {
                "case_id": case.case_id,
                "suite": case.suite,
                "runner": case.runner,
                "scenario": case.scenario,
                "tags": list(case.tags),
                "difficulty": case.difficulty,
                "status": "passed" if guard_ok else "failed",
                "failure_reason": "" if guard_ok else "guard_mismatch",
                "route_trace_present": None,
                "retrieval_trace_present": None,
                "tool_trace_present": None,
                "guard_present": decision.downgraded,
                "completion_integrity": True,
                "queue_integrity": None,
                "final_answer_present": bool(str(decision.final_answer or "").strip()),
                "tool_result_reflected": None,
                "route_correct": None,
                "skill_correct": None,
                "guard_correct": guard_ok,
                "trace_completeness": guard_ok,
                "latency_ms": elapsed_ms,
                "event_names": [],
                "outcome": {
                    "final_answer": decision.final_answer,
                    "trigger": actual_trigger,
                },
                "counts_numeric": bool(case.expect.get("counts_numeric")),
                "counts_locator": bool(case.expect.get("counts_locator")),
                "actual_guard": decision.downgraded,
                "judge_passed": None,
                "judge_result": None,
            }
        )
    return results


def _normalized_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _contains_all_terms(text: str, terms: list[str]) -> bool:
    normalized = _normalized_text(text)
    return all(_normalized_text(term) in normalized for term in terms if str(term).strip())


def _contains_any_terms(text: str, terms: list[str]) -> bool:
    normalized = _normalized_text(text)
    return any(_normalized_text(term) in normalized for term in terms if str(term).strip())


def _run_rewrite_planner_cases(
    cases: list[BenchmarkCase],
    *,
    use_live_llm_decisions: bool = True,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        plan = build_query_plan(case.message, prefer_llm=use_live_llm_decisions)
        rewritten_query = plan.query_variants[1] if len(plan.query_variants) > 1 else plan.query_variants[0]
        expect = dict(case.expect or {})
        preserved_terms = [str(item) for item in expect.get("must_preserve_terms", []) or [] if str(item).strip()]
        forbidden_terms = [str(item) for item in expect.get("must_not_introduce_terms", []) or [] if str(item).strip()]
        expected_question_type = str(expect.get("question_type", "") or "").strip()
        rewrite_preserves_intent = _contains_all_terms(" ".join(plan.query_variants), preserved_terms) if preserved_terms else True
        rewrite_drift_detected = _contains_any_terms(" ".join(plan.query_variants[1:]), forbidden_terms) if forbidden_terms else False
        planner_reasonable = (plan.question_type == expected_question_type) if expected_question_type else True
        failed: list[str] = []
        if not rewrite_preserves_intent:
            failed.append("rewrite_lost_required_terms")
        if rewrite_drift_detected:
            failed.append("rewrite_introduced_forbidden_terms")
        if not planner_reasonable:
            failed.append("planner_question_type_mismatch")
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        results.append(
            {
                "case_id": case.case_id,
                "suite": case.suite,
                "bucket": case.bucket,
                "runner": case.runner,
                "scenario": case.scenario,
                "tags": list(case.tags),
                "difficulty": case.difficulty,
                "status": "passed" if not failed else "failed",
                "failure_reason": ",".join(failed),
                "route_trace_present": None,
                "retrieval_trace_present": None,
                "tool_trace_present": None,
                "guard_present": None,
                "completion_integrity": True,
                "queue_integrity": None,
                "final_answer_present": None,
                "tool_result_reflected": None,
                "route_correct": None,
                "skill_correct": None,
                "guard_correct": None,
                "trace_completeness": not failed,
                "latency_ms": elapsed_ms,
                "event_names": [],
                "outcome": {
                    "original_query": case.message,
                    "question_type": plan.question_type,
                    "query_variants": list(plan.query_variants),
                    "rewritten_query": rewritten_query,
                    "entity_hints": list(plan.entity_hints),
                    "keyword_hints": list(plan.keyword_hints),
                    "rewrite_needed": plan.rewrite_needed,
                    "planner_reason": plan.planner_reason,
                    "planner_source": plan.planner_source,
                },
                "counts_numeric": None,
                "counts_locator": None,
                "actual_guard": None,
                "judge_passed": None,
                "judge_result": None,
                "llm_judge_passed": None,
                "llm_judge_score": None,
                "llm_judge_reason": "",
                "llm_judge_dimensions": {},
                "llm_judge_details": {},
                "llm_judge_error": "",
                "judge_disagreement": None,
                "rewrite_preserves_intent": rewrite_preserves_intent,
                "rewrite_drift_detected": rewrite_drift_detected,
                "planner_reasonable": planner_reasonable,
            }
        )
    return results


def _apply_benchmark_judge(
    cases: list[BenchmarkCase],
    results: list[dict[str, Any]],
    *,
    llm_judge: HarnessLLMJudge | None = None,
) -> list[dict[str, Any]]:
    judge = HarnessBenchmarkJudge()
    case_map = {case.case_id: case for case in cases}
    judged: list[dict[str, Any]] = []
    for result in results:
        enriched = dict(result)
        case = case_map.get(str(result.get("case_id", "")))
        if case is None:
            judged.append(enriched)
            continue
        enriched["bucket"] = case.bucket
        verdict = judge.judge_case(case, result)
        enriched["judge_passed"] = verdict.passed
        enriched["judge_result"] = verdict.to_dict()
        enriched["deterministic_judge_passed"] = verdict.passed
        enriched["deterministic_judge_result"] = verdict.to_dict()
        llm_verdict = (llm_judge or HarnessLLMJudge()).judge_case(
            case,
            enriched,
            deterministic_judge=verdict.to_dict(),
        )
        enriched["llm_judge_passed"] = llm_verdict.passed
        enriched["llm_judge_score"] = llm_verdict.score
        enriched["llm_judge_reason"] = llm_verdict.reason
        enriched["llm_judge_dimensions"] = dict(llm_verdict.dimensions)
        enriched["llm_judge_details"] = dict(llm_verdict.details)
        enriched["llm_judge_error"] = llm_verdict.error
        enriched["llm_judge_result"] = llm_verdict.to_dict()
        enriched["judge_disagreement"] = (
            verdict.passed != llm_verdict.passed
            if llm_verdict.passed is not None
            else None
        )
        if enriched.get("status") == "passed" and not verdict.passed:
            enriched["status"] = "failed"
            enriched["failure_reason"] = (str(enriched.get("failure_reason", "") or "") + ";judge_failed").strip(";")
        judged.append(enriched)
    return judged


async def run_selected_benchmark(
    *,
    suite: SuiteName = "contract",
    extra_case_files: list[str] | None = None,
    tag: str | None = None,
    limit: int | None = None,
    output_path: str | Path | None = None,
    use_llm_judge: bool = True,
    use_live_llm_decisions: bool = True,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    selected_cases = load_cases(suite=suite, extra_case_files=extra_case_files, tag=tag, limit=limit)
    grouped: dict[str, list[BenchmarkCase]] = {}
    for case in selected_cases:
        grouped.setdefault(case.suite, []).append(case)

    payload: dict[str, Any] = {
        "started_at": started_at,
        "selection": {
            "suite": suite,
            "tag": tag,
            "limit": limit,
            "case_files": [str(path) for path in resolve_case_files(suite, extra_case_files)],
            "use_live_llm_decisions": use_live_llm_decisions,
        },
        "summary": {},
        "suites": {},
        "cases": [],
        "judge": {},
    }
    llm_judge = HarnessLLMJudge.from_env() if use_llm_judge else HarnessLLMJudge(None)

    all_results: list[dict[str, Any]] = []
    for suite_name, suite_cases in grouped.items():
        suite_results: list[dict[str, Any]] = []
        runners = {case.runner for case in suite_cases}
        for runner in ("contract_lifecycle", "integration_lifecycle", "route_skill", "guard", "rewrite_planner"):
            if runner not in runners:
                continue
            runner_cases = [case for case in suite_cases if case.runner == runner]
            if runner == "contract_lifecycle":
                suite_results.extend(await _run_contract_lifecycle_cases(runner_cases))
            elif runner == "integration_lifecycle":
                suite_results.extend(
                    await _run_integration_lifecycle_cases(
                        runner_cases,
                        use_live_llm_decisions=use_live_llm_decisions,
                    )
                )
            elif runner == "route_skill":
                suite_results.extend(
                    await _run_route_skill_cases(
                        runner_cases,
                        use_live_llm_decisions=use_live_llm_decisions,
                    )
                )
            elif runner == "guard":
                suite_results.extend(_run_guard_cases(runner_cases))
            elif runner == "rewrite_planner":
                suite_results.extend(
                    _run_rewrite_planner_cases(
                        runner_cases,
                        use_live_llm_decisions=use_live_llm_decisions,
                    )
                )

        judged_suite_results = _apply_benchmark_judge(suite_cases, suite_results, llm_judge=llm_judge)
        payload["suites"][suite_name] = {
            "summary": summarize_results(judged_suite_results),
            "cases": judged_suite_results,
        }
        all_results.extend(judged_suite_results)

    payload["summary"] = summarize_results(all_results)
    payload["cases"] = all_results
    payload["judge"] = {
        "pass_rate": payload["summary"].get("judge_pass_rate"),
        "dimensions": payload["summary"].get("judge_dimensions", {}),
        "llm_pass_rate": payload["summary"].get("llm_judge_pass_rate"),
        "llm_dimensions": payload["summary"].get("llm_judge_dimensions", {}),
        "llm_available": llm_judge.available,
    }
    payload["completed_at"] = datetime.now(timezone.utc).isoformat()

    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
