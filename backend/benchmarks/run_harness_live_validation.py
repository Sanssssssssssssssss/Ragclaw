from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import AsyncMock, patch

import httpx
import uvicorn

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.storage_layout import harness_live_output_path
from src.backend.decision.execution_strategy import ExecutionStrategy
from src.backend.decision.lightweight_router import RoutingDecision
from src.backend.knowledge.types import Evidence, OrchestratedRetrievalResult, RetrievalStep
from src.backend.runtime.agent_manager import agent_manager
from src.backend.runtime.execution_support import HarnessExecutionSupport
from src.backend.runtime.runtime import build_harness_runtime


CASE_FILE = Path(__file__).resolve().parent / "harness_cases" / "live_validation_cases.json"


@dataclass(frozen=True)
class LiveValidationCase:
    case_id: str
    scenario: str
    message: str
    expect_route: str
    expect_done: bool = True
    expect_error: bool = False
    expect_queue: bool = False
    expect_retrieval: bool = False
    expect_tool: bool = False
    expect_guard: bool = False
    expect_final_contains: tuple[str, ...] = ()
    expect_final_excludes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    companion_message: str = ""
    model_answer: str = ""
    companion_model_answer: str = ""
    companion_fail: bool = False
    tool_outputs: tuple[str, ...] = ()
    retrieval_result: dict[str, Any] | None = None


@dataclass
class LiveCaseResult:
    case_id: str
    scenario: str
    status: str
    failure_reason: str
    session_id: str
    run_ids: list[str] = field(default_factory=list)
    sse_events: list[str] = field(default_factory=list)
    final_answer: str = ""
    trace_events: list[str] = field(default_factory=list)
    route_intent: str = ""
    expect_retrieval: bool = False
    expect_tool: bool = False
    guard_present: bool = False
    queued: bool = False
    done_present: bool = False
    error_present: bool = False
    retrieval_present: bool = False
    tool_present: bool = False
    completion_integrity: bool = False
    session_persisted: bool = False
    sse_order_ok: bool = False
    latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_live_cases() -> tuple[LiveValidationCase, ...]:
    payload = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    cases: list[LiveValidationCase] = []
    for raw in payload.get("cases", []):
        cases.append(
            LiveValidationCase(
                case_id=str(raw["case_id"]),
                scenario=str(raw.get("scenario", "")),
                message=str(raw.get("message", "")),
                expect_route=str(raw.get("expect_route", "")),
                expect_done=bool(raw.get("expect_done", True)),
                expect_error=bool(raw.get("expect_error", False)),
                expect_queue=bool(raw.get("expect_queue", False)),
                expect_retrieval=bool(raw.get("expect_retrieval", False)),
                expect_tool=bool(raw.get("expect_tool", False)),
                expect_guard=bool(raw.get("expect_guard", False)),
                expect_final_contains=tuple(str(item) for item in raw.get("expect_final_contains", [])),
                expect_final_excludes=tuple(str(item) for item in raw.get("expect_final_excludes", [])),
                tags=tuple(str(item) for item in raw.get("tags", [])),
                companion_message=str(raw.get("companion_message", "")),
                model_answer=str(raw.get("model_answer", "")),
                companion_model_answer=str(raw.get("companion_model_answer", "")),
                companion_fail=bool(raw.get("companion_fail", False)),
                tool_outputs=tuple(str(item) for item in raw.get("tool_outputs", [])),
                retrieval_result=dict(raw["retrieval_result"]) if raw.get("retrieval_result") is not None else None,
            )
        )
    return tuple(cases)


DEFAULT_CASES = _load_live_cases()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live HTTP validation against the harness production path.")
    parser.add_argument("--case", action="append", default=[], help="Only run one or more case ids.")
    parser.add_argument("--tag", default=None, help="Only run cases containing one tag.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of loaded cases.")
    parser.add_argument("--output", default=str(harness_live_output_path()), help="JSON output path.")
    return parser.parse_args(argv)


def _select_cases(*, case_ids: list[str] | None = None, tag: str | None = None, limit: int | None = None) -> list[LiveValidationCase]:
    selected = list(DEFAULT_CASES)
    if case_ids:
        wanted = set(case_ids)
        selected = [case for case in selected if case.case_id in wanted]
    if tag:
        selected = [case for case in selected if tag in case.tags]
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    return selected


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _parse_sse_payload(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for chunk in text.split("\n\n"):
        if not chunk.strip():
            continue
        event_name = ""
        payload: dict[str, Any] = {}
        for line in chunk.splitlines():
            if line.startswith("event: "):
                event_name = line[len("event: ") :].strip()
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
        if event_name:
            events.append((event_name, payload))
    return events


class _LiveValidationToolMessage:
    def __init__(self, *, message_type: str, content: str = "", tool_calls=None, tool_call_id: str = "", name: str = "") -> None:
        self.type = message_type
        self.content = content
        self.tool_calls = list(tool_calls or [])
        self.tool_call_id = tool_call_id
        self.name = name


class _LiveValidationToolAgent:
    def __init__(self, case: LiveValidationCase) -> None:
        self._case = case

    async def astream(self, _inputs, stream_mode=None):
        tool_outputs = list(self._case.tool_outputs or ())
        if not tool_outputs:
            return
        for index, output in enumerate(tool_outputs, start=1):
            call_id = f"{self._case.case_id}-tool-{index}"
            yield (
                "updates",
                {
                    "tool_node": {
                        "messages": [
                            _LiveValidationToolMessage(
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
                            _LiveValidationToolMessage(
                                message_type="tool",
                                tool_call_id=call_id,
                                name="terminal",
                                content=output,
                            )
                        ]
                    }
                },
            )


def _dict_to_retrieval_result(payload: dict[str, Any]) -> OrchestratedRetrievalResult:
    evidences = [
        Evidence(
            source_path=str(item.get("source_path", "") or ""),
            source_type=str(item.get("source_type", "pdf") or "pdf"),
            locator=str(item.get("locator", "") or ""),
            snippet=str(item.get("snippet", "") or ""),
            channel=str(item.get("channel", "fused") or "fused"),
            score=float(item.get("score")) if item.get("score") is not None else None,
        )
        for item in payload.get("evidences", []) or []
    ]
    steps = [
        RetrievalStep(
            kind="knowledge",
            stage="fused",
            title="Live validation retrieval",
            message=str(payload.get("reason", "") or "live validation retrieval"),
            results=evidences,
        )
    ] if evidences else []
    return OrchestratedRetrievalResult(
        status=str(payload.get("status", "success") or "success"),
        evidences=evidences,
        steps=steps,
        reason=str(payload.get("reason", "") or ""),
        question_type=str(payload.get("question_type", "direct_fact") or "direct_fact"),
        entity_hints=[str(item) for item in payload.get("entity_hints", []) or []],
    )


class _LiveValidationSupport(HarnessExecutionSupport):
    def __init__(self, cases: list[LiveValidationCase]) -> None:
        super().__init__(agent_manager)
        self._cases = {case.message: case for case in cases}
        for case in cases:
            if case.companion_message:
                self._cases[case.companion_message] = case
        self._active_message = ""

    def set_active_message(self, message: str) -> None:
        self._active_message = str(message or "")

    def case_for_message(self, message: str) -> LiveValidationCase:
        return self._cases[str(message or "").strip()]

    def active_case(self) -> LiveValidationCase:
        return self.case_for_message(self._active_message)

    async def astream_model_answer(self, messages: list[dict[str, str]], extra_instructions=None, system_prompt_override=None):
        message = str(messages[-1]["content"] or "")
        case = self.case_for_message(message)
        is_companion = bool(case.companion_message and message == case.companion_message)
        if case.scenario == "failure" or (is_companion and case.companion_fail):
            raise RuntimeError("live validation failure")

        if case.scenario == "failure_after_partial":
            yield {"type": "token", "content": "partial live "}
            raise RuntimeError("live validation partial failure")

        if is_companion and case.companion_model_answer:
            final_text = case.companion_model_answer
            await asyncio.sleep(0.35)
        elif extra_instructions and any("tool calls already succeeded" in str(item).lower() for item in extra_instructions):
            final_text = case.model_answer or "Tool results were synthesized."
        elif case.scenario == "queue" and case.companion_message:
            final_text = case.model_answer or "queued live answer"
        else:
            final_text = case.model_answer or "live direct answer"

        midpoint = max(1, len(final_text) // 2)
        yield {"type": "token", "content": final_text[:midpoint]}
        yield {"type": "token", "content": final_text[midpoint:]}
        yield {"type": "done", "content": final_text, "usage": {"input_tokens": 12, "output_tokens": 6}}

    async def knowledge_astream(self, message: str):
        case = self.case_for_message(message)
        if case.retrieval_result is None:
            return
        yield {"type": "orchestrated_result", "result": _dict_to_retrieval_result(case.retrieval_result)}

    def build_tool_agent(self, *, extra_instructions=None, tools_override=None):
        return _LiveValidationToolAgent(self.active_case())


def _summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item.get("status") == "passed")
    queue_cases = [item for item in results if item.get("scenario") == "queue"]
    return {
        "total_cases": total,
        "passed_cases": passed,
        "failed_cases": total - passed,
        "queue_integrity": round(sum(1 for item in queue_cases if item.get("queued")) / len(queue_cases), 4) if queue_cases else None,
        "guard_trace_presence": round(sum(1 for item in results if item.get("guard_present")) / max(1, sum(1 for item in results if item.get("expect_guard"))), 4) if any(item.get("expect_guard") for item in results) else None,
        "retrieval_trace_presence": round(sum(1 for item in results if item.get("retrieval_present")) / max(1, sum(1 for item in results if item.get("expect_retrieval"))), 4) if any(item.get("expect_retrieval") for item in results) else None,
        "tool_trace_presence": round(sum(1 for item in results if item.get("tool_present")) / max(1, sum(1 for item in results if item.get("expect_tool"))), 4) if any(item.get("expect_tool") for item in results) else None,
        "completion_integrity": round(sum(1 for item in results if item.get("completion_integrity")) / max(1, total), 4) if total else None,
        "session_persistence_integrity": round(sum(1 for item in results if item.get("session_persisted")) / max(1, total), 4) if total else None,
        "sse_order_integrity": round(sum(1 for item in results if item.get("sse_order_ok")) / max(1, total), 4) if total else None,
    }


@contextmanager
def _serve_app() -> Iterator[str]:
    from backend import app as backend_app

    port = _find_free_port()
    config = uvicorn.Config(backend_app.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                response = httpx.get(f"{base_url}/health", timeout=1.0)
                if response.status_code == 200:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError("live validation server did not become healthy in time")
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _collect_trace_by_message(runtime, message: str) -> dict[str, Any]:
    root = runtime._deps.trace_store.root_dir  # noqa: SLF001
    for trace_path in sorted(root.glob("*.jsonl"), reverse=True):
        run_id = trace_path.stem
        trace = runtime._deps.trace_store.read_trace(run_id)  # noqa: SLF001
        metadata = trace.get("metadata") or {}
        if str(metadata.get("user_message", "") or "") == message:
            return trace
    raise FileNotFoundError(f"no trace found for message={message!r}")


async def _create_session(client: httpx.AsyncClient, title: str) -> str:
    response = await client.post("/api/sessions", json={"title": title})
    response.raise_for_status()
    payload = response.json()
    return str(payload["id"])


async def _delete_session(client: httpx.AsyncClient, session_id: str) -> None:
    response = await client.delete(f"/api/sessions/{session_id}")
    response.raise_for_status()


async def _fetch_session_messages(client: httpx.AsyncClient, session_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"/api/sessions/{session_id}/messages")
    response.raise_for_status()
    payload = response.json()
    return list(payload.get("messages", []))


async def _post_stream(client: httpx.AsyncClient, *, session_id: str, message: str) -> tuple[list[tuple[str, dict[str, Any]]], int]:
    started = time.perf_counter()
    response = await client.post(
        "/api/chat",
        json={"message": message, "session_id": session_id, "stream": True},
        timeout=40.0,
    )
    response.raise_for_status()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return _parse_sse_payload(response.text), elapsed_ms


def _sse_order_ok(names: list[str]) -> bool:
    if not names:
        return False
    if "token" in names and "done" in names and names.index("token") > names.index("done"):
        return False
    if "run.queued" in names and "run.dequeued" in names and names.index("run.queued") > names.index("run.dequeued"):
        return False
    return True


def _result_from_trace(
    case: LiveValidationCase,
    session_id: str,
    sse_events: list[tuple[str, dict[str, Any]]],
    latency_ms: int,
    trace: dict[str, Any],
    session_messages: list[dict[str, Any]],
) -> LiveCaseResult:
    names = [name for name, _payload in sse_events]
    outcome = trace.get("outcome") or {}
    trace_events = [str(item.get("name", "")) for item in trace.get("events", [])]
    route_event = next((item for item in trace.get("events", []) if item.get("name") == "route.decided"), {})
    final_answer = str(outcome.get("final_answer", "") or "")
    failure_reason: list[str] = []

    if case.expect_route and str(route_event.get("payload", {}).get("intent", "") or "") != case.expect_route:
        failure_reason.append("route_mismatch")
    if case.expect_done and "done" not in names:
        failure_reason.append("missing_done")
    if case.expect_error and "error" not in names:
        failure_reason.append("missing_error")
    if case.expect_queue and not {"run.queued", "run.dequeued"}.issubset(set(names)):
        failure_reason.append("missing_queue_sse")
    if case.expect_retrieval and "retrieval.completed" not in trace_events:
        failure_reason.append("missing_retrieval_trace")
    if case.expect_tool and not {"tool.started", "tool.completed"}.issubset(set(trace_events)):
        failure_reason.append("missing_tool_trace")
    if case.expect_guard and "guard.failed" not in trace_events:
        failure_reason.append("missing_guard_trace")
    for token in case.expect_final_contains:
        if token not in final_answer:
            failure_reason.append(f"missing_final:{token}")
    for token in case.expect_final_excludes:
        if token in final_answer:
            failure_reason.append(f"unexpected_final:{token}")

    completion_integrity = (
        ("run.failed" in trace_events and str(outcome.get("status", "") or "") == "failed")
        if case.expect_error
        else ("run.completed" in trace_events and str(outcome.get("status", "") or "") == "completed")
    )
    if not completion_integrity:
        failure_reason.append("completion_integrity_failed")

    assistant_messages = [item for item in session_messages if item.get("role") == "assistant"]
    session_persisted = bool(assistant_messages)
    if not session_persisted:
        failure_reason.append("session_not_persisted")

    sse_order_ok = _sse_order_ok(names)
    if not sse_order_ok:
        failure_reason.append("sse_order_invalid")

    return LiveCaseResult(
        case_id=case.case_id,
        scenario=case.scenario,
        status="passed" if not failure_reason else "failed",
        failure_reason=",".join(failure_reason),
        session_id=session_id,
        run_ids=[str(trace.get("run_id", "") or "")],
        sse_events=names,
        final_answer=final_answer,
        trace_events=trace_events,
        route_intent=str(route_event.get("payload", {}).get("intent", "") or ""),
        expect_retrieval=case.expect_retrieval,
        expect_tool=case.expect_tool,
        guard_present="guard.failed" in trace_events,
        queued={"run.queued", "run.dequeued"}.issubset(set(names)),
        done_present="done" in names,
        error_present="error" in names,
        retrieval_present="retrieval.completed" in trace_events,
        tool_present={"tool.started", "tool.completed"}.issubset(set(trace_events)),
        completion_integrity=completion_integrity,
        session_persisted=session_persisted,
        sse_order_ok=sse_order_ok,
        latency_ms=latency_ms,
    )


async def _run_one_case(client: httpx.AsyncClient, runtime, case: LiveValidationCase) -> dict[str, Any]:
    session_id = await _create_session(client, title=f"Live Validation {case.case_id}")
    try:
        if case.scenario == "queue":
            assert case.companion_message
            first = asyncio.create_task(_post_stream(client, session_id=session_id, message=case.companion_message))
            await asyncio.sleep(0.05)
            second_events, latency_ms = await _post_stream(client, session_id=session_id, message=case.message)
            try:
                await first
            except Exception:
                pass
            trace = _collect_trace_by_message(runtime, case.message)
            session_messages = await _fetch_session_messages(client, session_id)
            return _result_from_trace(case, session_id, second_events, latency_ms, trace, session_messages).to_dict()

        sse_events, latency_ms = await _post_stream(client, session_id=session_id, message=case.message)
        trace = _collect_trace_by_message(runtime, case.message)
        session_messages = await _fetch_session_messages(client, session_id)
        return _result_from_trace(case, session_id, sse_events, latency_ms, trace, session_messages).to_dict()
    finally:
        await _delete_session(client, session_id)


async def _run_live_cases(base_url: str, runtime, cases: list[LiveValidationCase]) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(base_url=base_url) as client:
        results: list[dict[str, Any]] = []
        for case in cases:
            results.append(await _run_one_case(client, runtime, case))
        return results


async def run_live_validation(
    *,
    case_ids: list[str] | None = None,
    tag: str | None = None,
    limit: int | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    cases = _select_cases(case_ids=case_ids, tag=tag, limit=limit)
    if not cases:
        raise ValueError("no live validation cases selected")

    support = _LiveValidationSupport(cases)
    started_at = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="harness-live-runs-") as temp_runs_dir, ExitStack() as stack:
        runtime = build_harness_runtime(Path(temp_runs_dir))
        from backend import app as backend_app

        original_resolve_routing = agent_manager.resolve_routing

        async def _resolve_with_tracking(message: str, history: list[dict[str, Any]]):
            case = support.case_for_message(message)
            support.set_active_message(message)
            if case.scenario == "failure":
                return (
                    ExecutionStrategy(allow_tools=False, allow_knowledge=False, allow_retrieval=False, force_direct_answer=True),
                    RoutingDecision(
                        intent="direct_answer",
                        needs_tools=False,
                        needs_retrieval=False,
                        allowed_tools=(),
                        confidence=1.0,
                        reason_short="live_validation_failure",
                        source="live_validation",
                        subtype="",
                    ),
                )
            return await original_resolve_routing(message, history)

        stack.enter_context(patch.object(backend_app, "refresh_snapshot", lambda *_args, **_kwargs: None))
        stack.enter_context(patch.object(backend_app.memory_indexer, "rebuild_index", lambda *_args, **_kwargs: None))
        stack.enter_context(patch.object(backend_app, "_warm_knowledge_index", AsyncMock(return_value=None)))
        stack.enter_context(patch.object(agent_manager, "get_harness_runtime", return_value=runtime))
        stack.enter_context(patch.object(agent_manager, "resolve_routing", side_effect=_resolve_with_tracking))
        stack.enter_context(patch.object(agent_manager, "create_execution_support", return_value=support))
        stack.enter_context(patch("src.backend.runtime.executors.memory_indexer.retrieve", return_value=[]))
        stack.enter_context(patch("src.backend.runtime.executors.knowledge_orchestrator.astream", side_effect=support.knowledge_astream))

        with _serve_app() as base_url:
            results = await _run_live_cases(base_url, runtime, cases)

    payload = {
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "summary": _summarize_results(results),
        "cases": results,
        "selection": {
            "case_ids": list(case_ids or []),
            "tag": tag,
            "limit": limit,
            "case_file": str(CASE_FILE),
        },
    }
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = asyncio.run(
        run_live_validation(
            case_ids=list(args.case or []),
            tag=args.tag,
            limit=args.limit,
            output_path=args.output,
        )
    )
    print(args.output)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
