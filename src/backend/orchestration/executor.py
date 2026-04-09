from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langgraph.types import Command, interrupt

from src.backend.capabilities.invocation import CapabilityRuntimeContext, GovernedCapabilityTool, capability_runtime_scope, invoke_capability
from src.backend.capabilities.types import CapabilityResult
from src.backend.context import ContextAssembler, ContextWriter
from src.backend.context.models import ContextAssembly, ContextTurnSnapshot
from src.backend.context.store import context_store
from src.backend.decision.skill_gate import SkillDecision, skill_instruction
from src.backend.observability.types import AnswerRecord, RetrievalRecord, RouteDecisionRecord, SkillDecisionRecord, ToolCallRecord
from src.backend.orchestration.checkpointing import PendingHitlRequest, checkpoint_store
from src.backend.orchestration.compiler import compile_harness_orchestration_graph
from src.backend.orchestration.recovery import build_recovery_fallback_answer, build_recovery_hitl_request, extract_latest_failed_capability
from src.backend.orchestration.recovery_policies import select_recovery_action
from src.backend.orchestration.state import GraphState, create_initial_graph_state
from src.backend.runtime.graders import KnowledgeAnswerGrader

if TYPE_CHECKING:  # pragma: no cover
    from src.backend.decision.execution_strategy import ExecutionStrategy
    from src.backend.decision.lightweight_router import RoutingDecision
    from src.backend.runtime.agent_manager import AgentManager
    from src.backend.runtime.runtime import HarnessRuntime, RuntimeRunHandle


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text")
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
_EXPLICIT_CAPABILITY_IDS = {"mcp_filesystem_read_file", "mcp_filesystem_list_directory", "mcp_web_fetch_url"}


@dataclass
class _ExecutionBindings:
    runtime: "HarnessRuntime"
    handle: "RuntimeRunHandle"
    context: CapabilityRuntimeContext


class HarnessLangGraphOrchestrator:
    def __init__(
        self,
        agent_manager: "AgentManager",
        *,
        execution_support,
        knowledge_grader: KnowledgeAnswerGrader | None = None,
        resume_checkpoint_id: str = "",
        resume_thread_id: str = "",
        resume_source: str = "",
        resume_payload: dict[str, Any] | None = None,
    ) -> None:
        self._agent = agent_manager
        self._execution = execution_support
        self._knowledge_grader = knowledge_grader or KnowledgeAnswerGrader(agent_manager)
        self._graph = compile_harness_orchestration_graph(self)
        self._context_assembler = ContextAssembler(base_dir=self._agent.base_dir)
        self._context_writer = ContextWriter(base_dir=self._agent.base_dir)
        self._bindings: _ExecutionBindings | None = None
        self._resume_checkpoint_id = str(resume_checkpoint_id or "")
        self._resume_thread_id = str(resume_thread_id or "")
        self._resume_source = str(resume_source or "")
        self._resume_payload = dict(resume_payload or {})

    async def run(self, runtime: "HarnessRuntime", handle: "RuntimeRunHandle", *, message: str, history: list[dict[str, Any]]) -> GraphState:
        context = CapabilityRuntimeContext(
            runtime=runtime,
            handle=handle,
            registry=self._agent.get_capability_registry(),
            governor=runtime.governor_for(handle.run_id),
            approval_overrides=set(),
        )
        self._bindings = _ExecutionBindings(runtime=runtime, handle=handle, context=context)
        try:
            async with capability_runtime_scope(context):
                thread_id = self._thread_id_for(handle)
                if self._resume_checkpoint_id:
                    await self._emit_resume_events(runtime, handle, thread_id)
                    resume_input: Any = None
                    if self._resume_payload:
                        resume_input = Command(resume=dict(self._resume_payload))
                    result = await self._graph.ainvoke(
                        resume_input,
                        config={
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_id": self._resume_checkpoint_id,
                            }
                        },
                    )
                else:
                    result = await self._graph.ainvoke(
                        create_initial_graph_state(
                            run_id=handle.run_id,
                            session_id=getattr(handle.metadata, "session_id", None),
                            thread_id=thread_id,
                            user_message=message,
                            history=history,
                        ),
                        config={"configurable": {"thread_id": thread_id}},
                    )
                await self._emit_hitl_interrupt_if_needed(runtime, handle, thread_id, result)
                await self._emit_checkpoint_created(runtime, handle, thread_id)
                return result
        finally:
            self._bindings = None

    def _bindings_or_raise(self) -> _ExecutionBindings:
        if self._bindings is None:
            raise RuntimeError("orchestration bindings are not active")
        return self._bindings

    async def bootstrap_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        checkpoint_meta = {
            **dict(state.get("checkpoint_meta", {}) or {}),
            "thread_id": state.get("thread_id", ""),
            "checkpoint_id": self._resume_checkpoint_id or str(state.get("checkpoint_meta", {}).get("checkpoint_id", "") or ""),
            "resume_source": self._resume_source or str(state.get("checkpoint_meta", {}).get("resume_source", "") or ""),
            "run_status": "resumed" if self._resume_checkpoint_id else str(state.get("checkpoint_meta", {}).get("run_status", "") or "fresh"),
            "updated_at": bindings.runtime.now(),
        }
        base_state = {
            **dict(state),
            "checkpoint_meta": checkpoint_meta,
            "augmented_history": list(state.get("history", [])),
            "rag_mode": self._agent._runtime_rag_mode(),
            "governor_snapshot": bindings.runtime.governor_for(bindings.handle.run_id).snapshot(),
        }
        context_updates = self._context_writer.snapshot(base_state, updated_at=bindings.runtime.now())
        return {
            "augmented_history": list(state.get("history", [])),
            "rag_mode": self._agent._runtime_rag_mode(),
            "governor_snapshot": bindings.runtime.governor_for(bindings.handle.run_id).snapshot(),
            "checkpoint_meta": checkpoint_meta,
            **context_updates,
        }

    async def route_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        route_context = self._context_assembler.assemble(path_kind="direct_answer", state=state, call_site="route")
        strategy, decision = await self._agent.resolve_routing(state["user_message"], list(route_context.history_messages))
        await bindings.runtime.emit(
            bindings.handle,
            "route.decided",
            RouteDecisionRecord(
                intent=decision.intent,
                needs_tools=decision.needs_tools,
                needs_retrieval=decision.needs_retrieval,
                allowed_tools=tuple(decision.allowed_tools),
                confidence=decision.confidence,
                reason_short=decision.reason_short,
                source=decision.source,
                subtype=decision.subtype,
                ambiguity_flags=tuple(decision.ambiguity_flags),
                escalated=decision.escalated,
                model_name=decision.model_name,
            ).to_dict(),
        )
        return {"execution_strategy": strategy, "route_decision": decision, "path_kind": self._path_kind_from_decision(decision)}

    async def skill_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        strategy = state.get("execution_strategy")
        decision = state.get("route_decision")
        if strategy is None or decision is None:
            return {}
        skill_context = self._context_assembler.assemble(
            path_kind=self._path_kind_from_decision(decision),
            state=state,
            call_site="skill",
        )
        skill = self._agent.decide_skill(state["user_message"], list(skill_context.history_messages), strategy, decision)
        if skill.use_skill:
            await self._activate_skill_capability(message=state["user_message"], routing_decision=decision, skill_decision=skill)
        await bindings.runtime.emit(
            bindings.handle,
            "skill.decided",
            SkillDecisionRecord(
                use_skill=skill.use_skill,
                skill_name=skill.skill_name,
                confidence=skill.confidence,
                reason_short=skill.reason_short,
            ).to_dict(),
        )
        updates = self._context_writer.snapshot({**dict(state), "skill_decision": skill}, updated_at=bindings.runtime.now())
        return {"skill_decision": skill, **updates}

    async def memory_retrieval_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        strategy = state.get("execution_strategy")
        if not state.get("rag_mode") or strategy is None or not strategy.allow_retrieval:
            updates = self._context_writer.snapshot({**dict(state), "memory_retrieval": []}, updated_at=bindings.runtime.now())
            return {"memory_retrieval": [], **updates}
        await bindings.runtime.emit(bindings.handle, "retrieval.started", {"kind": "memory", "stage": "memory", "title": "Memory retrieval", "message": ""})
        retrievals = self._memory_retrieve(state["user_message"])
        if not retrievals:
            updates = self._context_writer.snapshot({**dict(state), "memory_retrieval": []}, updated_at=bindings.runtime.now())
            return {"memory_retrieval": [], **updates}
        step = self._agent._format_memory_retrieval_step(retrievals)
        await bindings.runtime.emit(
            bindings.handle,
            "retrieval.completed",
            RetrievalRecord(
                kind=step["kind"],
                stage=step["stage"],
                title=step["title"],
                message=step["message"],
                results=tuple(self._agent._harness_retrieval_evidence_records(step["results"])),
            ).to_dict(),
        )
        updates = self._context_writer.snapshot(
            {**dict(state), "memory_retrieval": retrievals},
            updated_at=bindings.runtime.now(),
        )
        return {"memory_retrieval": retrievals, **updates}

    async def direct_answer_node(self, state: GraphState) -> dict[str, Any]:
        strategy = state.get("execution_strategy")
        assembly = self._context_assembler.assemble(path_kind="direct_answer", state=state, call_site="direct_answer")
        self._record_turn_context_snapshot(state=state, assembly=assembly, call_site="direct_answer", model_invoked=True)
        messages = list(assembly.history_messages)
        messages.append({"role": "user", "content": state["user_message"]})
        extra_instructions = list(assembly.extra_instructions)
        if strategy is not None:
            extra_instructions.extend(strategy.to_instructions())
        answer, usage = await self._stream_model_answer(messages, extra_instructions=extra_instructions or None)
        result = {"final_answer": answer, "answer_usage": usage, "answer_finalized": True, "answer_segments": [answer] if answer else []}
        updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=self._bindings_or_raise().runtime.now())
        return {**result, **updates}

    async def knowledge_retrieval_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        await bindings.runtime.emit(bindings.handle, "retrieval.started", {"kind": "knowledge", "stage": "knowledge", "title": "Knowledge retrieval", "message": ""})
        result = None
        async for event in self._knowledge_astream(state["user_message"]):
            if event.get("type") == "orchestrated_result":
                result = event["result"]
        if result is not None:
            for step in result.steps:
                await bindings.runtime.emit(
                    bindings.handle,
                    "retrieval.completed",
                    RetrievalRecord(
                        kind=step.kind,
                        stage=step.stage,
                        title=step.title,
                        message=step.message,
                        results=tuple(self._agent._harness_retrieval_evidence_records([item.to_dict() for item in step.results])),
                        status=getattr(result, "status", ""),
                        reason=getattr(result, "reason", ""),
                    ).to_dict(),
                )
        updates = self._context_writer.snapshot(
            {**dict(state), "knowledge_retrieval": result},
            updated_at=bindings.runtime.now(),
        )
        return {"knowledge_retrieval": result, **updates}

    async def knowledge_synthesis_node(self, state: GraphState) -> dict[str, Any]:
        result = state.get("knowledge_retrieval")
        assembly = self._context_assembler.assemble(path_kind="knowledge_qa", state=state, call_site="knowledge_synthesis")
        self._record_turn_context_snapshot(state=state, assembly=assembly, call_site="knowledge_synthesis", model_invoked=True)
        messages = list(assembly.history_messages)
        messages.append({"role": "user", "content": state["user_message"]})
        extra_instructions = list(assembly.extra_instructions)
        if result:
            extra_instructions.extend(self._agent._knowledge_answer_instructions(result))
        answer, usage = await self._stream_model_answer(
            messages,
            extra_instructions=extra_instructions or None,
            system_prompt_override=self._agent._knowledge_system_prompt(),
            stream_deltas=False,
        )
        result_payload = {"final_answer": answer, "answer_usage": usage}
        updates = self._context_writer.snapshot({**dict(state), **result_payload}, updated_at=self._bindings_or_raise().runtime.now())
        return {**result_payload, **updates}

    async def knowledge_guard_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        result = state.get("knowledge_retrieval")
        answer = str(state.get("final_answer", "") or "")
        guard_result = None
        if result is not None:
            graded = self._knowledge_grader.grade(answer, result)
            answer = graded.final_answer
            guard_result = graded.guard_result
            if guard_result is not None:
                await bindings.runtime.emit(bindings.handle, "guard.failed", guard_result.to_dict())
        await self._emit_final_answer(answer, usage=state.get("answer_usage"))
        result_payload = {"final_answer": answer, "guard_result": guard_result, "answer_finalized": True, "answer_segments": [answer] if answer else []}
        updates = self._context_writer.snapshot({**dict(state), **result_payload}, updated_at=bindings.runtime.now())
        return {**result_payload, **updates}

    async def capability_selection_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        strategy = state.get("execution_strategy")
        if strategy is None:
            updates = self._context_writer.snapshot({**dict(state), "selected_capabilities": []}, updated_at=bindings.runtime.now())
            return {"selected_capabilities": [], **updates}
        decision = state.get("route_decision")
        tools = self._agent._resolve_tools_for_strategy(strategy)
        if decision is not None and decision.allowed_tools:
            allowed_names = set(decision.allowed_tools)
            tools = [tool for tool in tools if getattr(tool, "name", "") in allowed_names]
        explicit_id, explicit_payload = self._explicit_capability_selection(state["user_message"], tools) if tools else ("", None)
        result = {
            "selected_capabilities": [str(getattr(tool, "name", "") or "") for tool in tools],
            "explicit_capability_id": explicit_id,
            "explicit_capability_payload": explicit_payload,
            "path_kind": "capability_path",
        }
        updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=bindings.runtime.now())
        return {**result, **updates}

    async def capability_approval_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        request = self._build_hitl_request(state)
        if request is None:
            return {"interrupt_request": None, "approval_decision": ""}

        response = interrupt(request)
        response_payload = dict(response) if isinstance(response, dict) else {"decision": response}
        decision = str(response_payload.get("decision", "") or "").strip().lower()
        if decision not in {"approve", "reject", "edit"}:
            decision = "reject"
        thread_id = str(request["thread_id"] or "")
        checkpoint_id = self._resume_checkpoint_id or str(request.get("checkpoint_id", "") or "")
        edited_input = (
            dict(response_payload.get("edited_input", {}) or {})
            if decision == "edit"
            else None
        )
        audited_request = checkpoint_store.get_hitl_request(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )
        audited_decision = checkpoint_store.get_hitl_decision(
            request_id=audited_request.request_id,
        ) if audited_request is not None else None
        if audited_request is not None and audited_decision is None:
            audited_request, audited_decision, _ = checkpoint_store.record_hitl_decision(
                thread_id=thread_id,
                checkpoint_id=checkpoint_id,
                decision=decision,
                actor_id=str(response_payload.get("actor_id", "") or f"session:{request.get('session_id') or thread_id}"),
                actor_type=str(response_payload.get("actor_type", "") or "session_user"),
                decided_at=str(response_payload.get("decided_at", "") or bindings.runtime.now()),
                resume_source=str(response_payload.get("resume_source", "") or self._resume_source or "langgraph_resume"),
                edited_input_snapshot=edited_input,
            )

        payload = {
            "request_id": str(getattr(audited_request, "request_id", "") or response_payload.get("request_id", "") or ""),
            "requested_at": str(getattr(audited_request, "requested_at", "") or ""),
            "decision_id": str(getattr(audited_decision, "decision_id", "") or response_payload.get("decision_id", "") or ""),
            "decision": decision,
            "actor_id": str(getattr(audited_decision, "actor_id", "") or response_payload.get("actor_id", "") or ""),
            "actor_type": str(getattr(audited_decision, "actor_type", "") or response_payload.get("actor_type", "") or ""),
            "decided_at": str(getattr(audited_decision, "decided_at", "") or response_payload.get("decided_at", "") or ""),
            "run_id": str(getattr(audited_request, "run_id", "") or request.get("run_id", "") or bindings.handle.run_id),
            "session_id": getattr(audited_request, "session_id", None) if audited_request is not None else request.get("session_id"),
            "thread_id": thread_id,
            "checkpoint_id": str(getattr(audited_request, "checkpoint_id", "") or checkpoint_id),
            "capability_id": request["capability_id"],
            "capability_type": request["capability_type"],
            "display_name": request["display_name"],
            "risk_level": request["risk_level"],
            "reason": request["reason"],
            "proposed_input": dict(request["proposed_input"]),
            "resume_source": str(
                getattr(audited_decision, "resume_source", "") or response_payload.get("resume_source", "") or self._resume_source or "hitl_api"
            ),
            "orchestration_engine": "langgraph",
        }
        if decision == "approve":
            payload["approved_input_snapshot"] = (
                dict(getattr(audited_decision, "approved_input_snapshot", {}) or {})
                if audited_decision is not None
                else dict(request["proposed_input"])
            )
        elif decision == "edit":
            payload["edited_input_snapshot"] = (
                dict(getattr(audited_decision, "edited_input_snapshot", {}) or {})
                if audited_decision is not None
                else dict(edited_input or request["proposed_input"])
            )
        else:
            payload["rejected_input_snapshot"] = (
                dict(getattr(audited_decision, "rejected_input_snapshot", {}) or {})
                if audited_decision is not None
                else dict(request["proposed_input"])
            )
        await bindings.runtime.emit(
            bindings.handle,
            "hitl.approved" if decision == "approve" else "hitl.edited" if decision == "edit" else "hitl.rejected",
            dict(payload),
        )
        if decision == "approve":
            bindings.context.approval_overrides.add(str(request["capability_id"]))
            result = {
                "interrupt_request": request,
                "approval_decision": "approve",
                "recovery_action": "",
            }
            updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=bindings.runtime.now())
            return {**result, **updates}
        if decision == "edit":
            bindings.context.approval_overrides.add(str(request["capability_id"]))
            result = {
                "interrupt_request": request,
                "approval_decision": "edit",
                "explicit_capability_id": str(request["capability_id"]),
                "explicit_capability_payload": dict(payload.get("edited_input_snapshot", {}) or request["proposed_input"]),
                "recovery_action": "",
            }
            updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=bindings.runtime.now())
            return {**result, **updates}

        blocked_result = CapabilityResult(
            status="blocked",
            payload={},
            partial=False,
            error_type="rejected_by_user",
            error_message=f"{request['display_name']} was rejected by the user before execution.",
            retryable=False,
            call_id=f"hitl-{request['capability_id']}",
            retry_count=0,
        )
        bindings.context.governor.record_result(
            self._agent.get_capability_registry().get(str(request["capability_id"])),
            blocked_result,
        )
        await bindings.runtime.emit(
            bindings.handle,
            "capability.blocked",
            {
                "run_id": bindings.handle.run_id,
                "session_id": getattr(bindings.handle.metadata, "session_id", None),
                "capability_id": request["capability_id"],
                "capability_type": request["capability_type"],
                "display_name": request["display_name"],
                "call_id": blocked_result.call_id,
                "status": blocked_result.status,
                "retry_count": 0,
                "partial": False,
                "latency_ms": 0,
                "error_type": blocked_result.error_type,
                "error_message": blocked_result.error_message,
                "input": dict(request["proposed_input"]),
                "payload": {},
                "risk_level": request["risk_level"],
                "approval_required": True,
                "budget_cost": 0,
                "request_id": payload["request_id"],
                "decision_id": payload["decision_id"],
            },
        )
        rejection_answer = (
            f"I did not run {request['display_name']} because you rejected this approval request."
        )
        rejection_context = self._context_assembler.assemble(
            path_kind="capability_path",
            state=state,
            call_site="hitl_rejection",
        )
        self._record_turn_context_snapshot(
            state=state,
            assembly=rejection_context,
            call_site="hitl_rejection",
            model_invoked=False,
        )
        await self._emit_final_answer(rejection_answer)
        result = {
            "interrupt_request": request,
            "approval_decision": "reject",
            "capability_results": [
                {
                    "capability_id": request["capability_id"],
                    "call_id": blocked_result.call_id,
                    "status": blocked_result.status,
                    "payload": {},
                    "error_type": blocked_result.error_type,
                    "error_message": blocked_result.error_message,
                }
            ],
            "final_answer": rejection_answer,
            "answer_segments": [rejection_answer],
            "answer_finalized": True,
            "needs_answer_synthesis": False,
            "recovery_action": "",
        }
        updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=bindings.runtime.now())
        return {**result, **updates}

    async def capability_invoke_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        if str(state.get("approval_decision", "") or "").strip().lower() == "reject":
            return {}
        strategy = state.get("execution_strategy")
        selected_capabilities = set(str(item or "") for item in state.get("selected_capabilities", []) or [])
        tools = []
        if strategy is not None:
            tools = [
                tool
                for tool in self._agent._resolve_tools_for_strategy(strategy)
                if str(getattr(tool, "name", "") or "") in selected_capabilities
            ]
        if not tools:
            updates = self._context_writer.snapshot({**dict(state), "selected_capabilities": []}, updated_at=bindings.runtime.now())
            return {"selected_capabilities": [], **updates}
        explicit_id = str(state.get("explicit_capability_id", "") or "")
        explicit_payload = state.get("explicit_capability_payload")
        if explicit_id and explicit_payload is not None:
            tool = next((item for item in tools if str(getattr(item, "name", "") or "") == explicit_id), tools[0])
            call_id = f"explicit-{explicit_id}"
            tool_input = json.dumps(explicit_payload, ensure_ascii=False)
            await bindings.runtime.emit(bindings.handle, "tool.started", ToolCallRecord(tool=explicit_id, input=tool_input, call_id=call_id).to_dict())
            result = await tool.aexecute_capability(explicit_payload)
            rendered = tool.render_capability_result(result)
            await bindings.runtime.emit(bindings.handle, "tool.completed", ToolCallRecord(tool=explicit_id, input=tool_input, output=rendered, call_id=call_id).to_dict())
            result_entry = {
                "capability_id": explicit_id,
                "capability_type": str(getattr(tool.capability_spec, "capability_type", "") or ""),
                "display_name": str(getattr(tool.capability_spec, "display_name", "") or explicit_id),
                "risk_level": str(getattr(tool.capability_spec, "risk_level", "") or ""),
                "approval_required": bool(getattr(tool.capability_spec, "approval_required", False)),
                "call_id": result.call_id,
                "status": result.status,
                "payload": dict(result.payload),
                "error_type": result.error_type,
                "error_message": result.error_message,
                "retry_count": result.retry_count,
                "input": dict(explicit_payload),
            }
            if result.status in {"success", "partial"}:
                direct_output_context = self._context_assembler.assemble(
                    path_kind="capability_path",
                    state=state,
                    call_site="capability_direct_output",
                )
                self._record_turn_context_snapshot(
                    state=state,
                    assembly=direct_output_context,
                    call_site="capability_direct_output",
                    model_invoked=False,
                )
                await self._emit_final_answer(rendered)
                result_payload = {
                    "recorded_tools": [{"tool": explicit_id, "input": tool_input, "output": rendered, "call_id": call_id}],
                    "capability_results": [result_entry],
                    "final_answer": rendered,
                    "answer_segments": [rendered] if rendered else [],
                    "answer_finalized": True,
                    "needs_answer_synthesis": False,
                    "last_failure": None,
                    "recovery_action": "",
                    "recovered_from_failure": bool(state.get("last_failure")),
                }
                updates = self._context_writer.snapshot({**dict(state), **result_payload}, updated_at=bindings.runtime.now())
                return {**result_payload, **updates}
            result_payload = {
                "recorded_tools": [{"tool": explicit_id, "input": tool_input, "output": rendered, "call_id": call_id}],
                "capability_results": [result_entry],
                "final_answer": "",
                "answer_segments": [],
                "answer_finalized": False,
                "needs_answer_synthesis": False,
                "last_failure": result_entry,
                "recovery_action": "",
                "recovered_from_failure": False,
            }
            updates = self._context_writer.snapshot({**dict(state), **result_payload}, updated_at=bindings.runtime.now())
            return {**result_payload, **updates}
        return await self._invoke_tool_path(
            state=state,
            message=state["user_message"],
            strategy=state.get("execution_strategy"),
            skill_decision=state.get("skill_decision"),
            allowed_tools=tools,
        )

    async def capability_recovery_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        failure = extract_latest_failed_capability(state)
        if failure is None:
            result = {
                "last_failure": None,
                "recovery_action": "",
                "recovery_metadata": dict(state.get("recovery_metadata", {}) or {}),
            }
            updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=bindings.runtime.now())
            return {**result, **updates}

        spec = self._agent.get_capability_registry().get(failure.capability_id)
        recovery_attempts = dict(state.get("recovery_attempts", {}) or {})
        recovery_metadata = dict(state.get("recovery_metadata", {}) or {})
        retry_count = int(recovery_attempts.get(failure.failure_key, 0) or 0)
        escalated_failures = set(str(item) for item in recovery_metadata.get("escalated_failures", []) or [])
        decision = select_recovery_action(
            spec=spec,
            error_type=failure.error_type,
            retry_count=retry_count,
            already_escalated=failure.failure_key in escalated_failures,
        )
        base_payload = {
            "run_id": bindings.handle.run_id,
            "session_id": state.get("session_id"),
            "thread_id": state.get("thread_id"),
            "capability_id": failure.capability_id,
            "capability_type": failure.capability_type,
            "display_name": failure.display_name,
            "error_type": failure.error_type,
            "error_message": failure.error_message,
            "recovery_action": decision.action,
            "retry_count": retry_count,
            "from_checkpoint": bool(self._resume_checkpoint_id),
            "recovered": False,
            "checkpoint_id": self._resume_checkpoint_id or str(state.get("checkpoint_meta", {}).get("checkpoint_id", "") or ""),
        }
        await bindings.runtime.emit(bindings.handle, "recovery.started", dict(base_payload))

        if decision.action == "retry_once":
            recovery_attempts[failure.failure_key] = retry_count + 1
            await bindings.runtime.emit(
                bindings.handle,
                "recovery.retrying",
                {
                    **base_payload,
                    "retry_count": retry_count + 1,
                    "recovered": True,
                },
            )
            result = {
                "recovery_attempts": recovery_attempts,
                "last_failure": failure.to_dict(),
                "recovery_action": "retry_once",
                "recovered_from_failure": False,
                "recovery_metadata": {
                    **recovery_metadata,
                    "last_decision_reason": decision.reason,
                    "last_failure_key": failure.failure_key,
                },
                "answer_finalized": False,
                "approval_decision": "",
            }
            updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=bindings.runtime.now())
            return {**result, **updates}

        if decision.action == "escalate_to_hitl":
            escalated_failures.add(failure.failure_key)
            interrupt_request = build_recovery_hitl_request(
                state=state,
                failure=failure,
                checkpoint_id=self._resume_checkpoint_id,
                reason=f"Recovery escalation for {failure.display_name}: {decision.reason}",
            )
            await bindings.runtime.emit(
                bindings.handle,
                "recovery.escalated",
                {
                    **base_payload,
                    "recovered": False,
                },
            )
            result = {
                "interrupt_request": interrupt_request,
                "approval_decision": "",
                "recovery_action": "escalate_to_hitl",
                "last_failure": failure.to_dict(),
                "recovery_metadata": {
                    **recovery_metadata,
                    "escalated_failures": sorted(escalated_failures),
                    "last_decision_reason": decision.reason,
                    "last_failure_key": failure.failure_key,
                },
                "answer_finalized": False,
            }
            updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=bindings.runtime.now())
            return {**result, **updates}

        fail_fast = decision.action == "fail_fast"
        answer = build_recovery_fallback_answer(
            failure=failure,
            recovered=retry_count > 0,
            fail_fast=fail_fast,
        )
        await bindings.runtime.emit(
            bindings.handle,
            "recovery.failed" if fail_fast else "recovery.fallback",
            {
                **base_payload,
                "recovered": False,
            },
        )
        recovery_context = self._context_assembler.assemble(
            path_kind="recovery_path",
            state=state,
            call_site="recovery_fallback",
        )
        self._record_turn_context_snapshot(
            state=state,
            assembly=recovery_context,
            call_site="recovery_fallback",
            model_invoked=False,
        )
        await self._emit_final_answer(answer)
        result = {
            "final_answer": answer,
            "answer_segments": [answer] if answer else [],
            "answer_finalized": True,
            "needs_answer_synthesis": False,
            "recovery_action": decision.action,
            "last_failure": failure.to_dict(),
            "recovery_metadata": {
                **recovery_metadata,
                "last_decision_reason": decision.reason,
                "last_failure_key": failure.failure_key,
            },
        }
        updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=bindings.runtime.now())
        return {**result, **updates}

    async def capability_synthesis_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        if state.get("answer_finalized"):
            return {}
        final_answer = str(state.get("final_answer", "") or "")
        recorded_tools = list(state.get("recorded_tools", []))
        if state.get("needs_answer_synthesis"):
            final_answer = await self._stream_tool_result_fallback(
                state=state,
                user_message=state["user_message"],
                recorded_tools=recorded_tools,
                strategy=state.get("execution_strategy"),
            )
        elif not final_answer and recorded_tools:
            final_answer = "\n\n".join(
                str(item.get("output", "") or "").strip()
                for item in recorded_tools
                if str(item.get("output", "") or "").strip()
            )
        if final_answer:
            if not state.get("needs_answer_synthesis"):
                synthesis_context = self._context_assembler.assemble(
                    path_kind="capability_path",
                    state=state,
                    call_site="capability_output_join",
                )
                self._record_turn_context_snapshot(
                    state=state,
                    assembly=synthesis_context,
                    call_site="capability_output_join",
                    model_invoked=False,
                )
            await bindings.runtime.emit(bindings.handle, "answer.completed", AnswerRecord(content=final_answer, segment_index=bindings.runtime.current_segment_index(bindings.handle), final=True).to_dict())
        result = {"final_answer": final_answer, "answer_finalized": True}
        updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=bindings.runtime.now())
        return {**result, **updates}

    async def capability_guard_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        result = {"guard_result": None}
        updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=bindings.runtime.now())
        return {**result, **updates}

    async def finalize_node(self, state: GraphState) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        result = {"governor_snapshot": bindings.runtime.governor_for(bindings.handle.run_id).snapshot()}
        updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=bindings.runtime.now())
        return {**result, **updates}

    def _thread_id_for(self, handle: "RuntimeRunHandle") -> str:
        return self._resume_thread_id or checkpoint_store.thread_id_for(
            session_id=getattr(handle.metadata, "session_id", None),
            run_id=handle.run_id,
        )

    async def _emit_resume_events(self, runtime: "HarnessRuntime", handle: "RuntimeRunHandle", thread_id: str) -> None:
        summary = checkpoint_store.get_checkpoint(thread_id=thread_id, checkpoint_id=self._resume_checkpoint_id)
        if summary is None:
            raise RuntimeError(f"checkpoint not found: {self._resume_checkpoint_id}")
        await runtime.emit(
            handle,
            "checkpoint.resumed",
            {
                "thread_id": thread_id,
                "checkpoint_id": summary.checkpoint_id,
                "resume_source": self._resume_source or "checkpoint",
                "orchestration_engine": "langgraph",
                "state_label": summary.state_label,
                "created_at": summary.created_at,
            },
        )

    async def _emit_checkpoint_created(self, runtime: "HarnessRuntime", handle: "RuntimeRunHandle", thread_id: str) -> None:
        latest = checkpoint_store.latest_checkpoint(thread_id=thread_id)
        if latest is None:
            return
        await runtime.emit(
            handle,
            "checkpoint.created",
            {
                "thread_id": latest.thread_id,
                "checkpoint_id": latest.checkpoint_id,
                "created_at": latest.created_at,
                "state_label": latest.state_label,
                "resume_eligible": latest.resume_eligible,
                "orchestration_engine": "langgraph",
            },
        )

    async def _emit_hitl_interrupt_if_needed(
        self,
        runtime: "HarnessRuntime",
        handle: "RuntimeRunHandle",
        thread_id: str,
        result: GraphState,
    ) -> None:
        interrupts = list(result.get("__interrupt__", []) or []) if isinstance(result, dict) else []
        if not interrupts:
            return
        latest = checkpoint_store.latest_checkpoint(thread_id=thread_id)
        if latest is None:
            return
        raw_payload = getattr(interrupts[0], "value", {}) or {}
        request, created = checkpoint_store.record_pending_hitl(
            PendingHitlRequest(
                request_id="",
                run_id=str(raw_payload.get("run_id", "") or handle.run_id),
                thread_id=str(raw_payload.get("thread_id", "") or thread_id),
                session_id=str(raw_payload.get("session_id")) if raw_payload.get("session_id") is not None else None,
                checkpoint_id=str(latest.checkpoint_id or raw_payload.get("checkpoint_id", "") or ""),
                capability_id=str(raw_payload.get("capability_id", "") or ""),
                capability_type=str(raw_payload.get("capability_type", "") or ""),
                display_name=str(raw_payload.get("display_name", "") or ""),
                risk_level=str(raw_payload.get("risk_level", "") or ""),
                reason=str(raw_payload.get("reason", "") or ""),
                proposed_input=dict(raw_payload.get("proposed_input", {}) or {}),
                requested_at=runtime.now(),
            )
        )
        if not created:
            return
        await runtime.emit(
            handle,
            "checkpoint.interrupted",
            {
                "thread_id": request.thread_id,
                "checkpoint_id": request.checkpoint_id,
                "resume_source": self._resume_source or "hitl_api",
                "orchestration_engine": "langgraph",
                "state_label": "interrupted",
                "created_at": latest.created_at,
            },
        )
        await runtime.emit(
            handle,
            "hitl.requested",
            {
                **request.to_dict(),
                "orchestration_engine": "langgraph",
                "resume_source": self._resume_source or "hitl_api",
            },
        )

    def _path_kind_from_decision(self, decision: "RoutingDecision") -> str:
        if decision.intent == "knowledge_qa":
            return "knowledge_qa"
        if decision.intent == "direct_answer" or (not decision.needs_tools and not decision.needs_retrieval):
            return "direct_answer"
        return "capability_path"

    async def _activate_skill_capability(self, *, message: str, routing_decision: "RoutingDecision", skill_decision: SkillDecision) -> None:
        skill_key = skill_decision.skill_name.replace("-", "_")
        spec = self._agent.get_capability_registry().get(f"skill.{skill_key}")
        await invoke_capability(
            spec=spec,
            payload={"message": message, "allowed_capabilities": list(getattr(routing_decision, "allowed_tools", ()) or ())},
            execute_async=self._build_skill_runner(spec, skill_decision),
        )

    def _build_skill_runner(self, spec, skill_decision: SkillDecision):
        async def _runner(_payload: dict[str, Any]) -> CapabilityResult:
            return CapabilityResult(status="success", payload={"capability_id": spec.capability_id, "guidance": skill_instruction(skill_decision.skill_name), "reason_short": skill_decision.reason_short, "confidence": skill_decision.confidence}, partial=False)
        return _runner

    def _build_hitl_request(self, state: GraphState) -> dict[str, Any] | None:
        existing_request = state.get("interrupt_request")
        if isinstance(existing_request, dict) and existing_request:
            return dict(existing_request)
        selected_capabilities = [str(item or "") for item in state.get("selected_capabilities", []) or []]
        if not selected_capabilities:
            return None
        registry = self._agent.get_capability_registry()
        selected_specs = []
        for capability_id in selected_capabilities:
            try:
                spec = registry.get(capability_id)
            except KeyError:
                continue
            if spec.approval_required:
                selected_specs.append(spec)
        if not selected_specs:
            return None
        spec = selected_specs[0]
        proposed_input = state.get("explicit_capability_payload")
        if not isinstance(proposed_input, dict) or not proposed_input:
            proposed_input = self._approval_proposed_input(spec.capability_id, state["user_message"])
        return {
            "run_id": state["run_id"],
            "thread_id": state.get("thread_id", ""),
            "session_id": state.get("session_id"),
            "capability_id": spec.capability_id,
            "capability_type": spec.capability_type,
            "display_name": spec.display_name,
            "risk_level": spec.risk_level,
            "reason": f"{spec.display_name} requires explicit approval before execution.",
            "proposed_input": dict(proposed_input),
            "checkpoint_id": str(state.get("checkpoint_meta", {}).get("checkpoint_id", "") or ""),
        }

    def _approval_proposed_input(self, capability_id: str, user_message: str) -> dict[str, Any]:
        normalized = str(user_message or "").strip()
        if capability_id == "python_repl":
            print_match = re.search(r"(print\s*\([^)]+\))", normalized, re.IGNORECASE)
            if print_match:
                return {"code": print_match.group(1)}
            calc_match = re.search(r"\bcalculate\s+(.+?)(?:,|and tell|then tell|$)", normalized, re.IGNORECASE)
            if calc_match:
                expression = calc_match.group(1).strip().rstrip(".")
                return {"code": f"print({expression})"}
            return {"code": normalized}
        return {"message": normalized}

    async def _stream_model_answer(self, messages: list[dict[str, str]], *, extra_instructions: list[str] | None = None, system_prompt_override: str | None = None, stream_deltas: bool = True) -> tuple[str, dict[str, int] | None]:
        bindings = self._bindings_or_raise()
        started = False
        final_answer = ""
        usage = None
        async for event in self._execution.astream_model_answer(messages, extra_instructions=extra_instructions, system_prompt_override=system_prompt_override):
            event_type = str(event.get("type", "") or "")
            if event_type == "token":
                if not started and stream_deltas:
                    await bindings.runtime.emit(bindings.handle, "answer.started", AnswerRecord(content="", segment_index=bindings.runtime.current_segment_index(bindings.handle), final=False).to_dict())
                    started = True
                content = str(event.get("content", "") or "")
                if content:
                    final_answer += content
                    if stream_deltas:
                        await bindings.runtime.emit(bindings.handle, "answer.delta", AnswerRecord(content=content, segment_index=bindings.runtime.current_segment_index(bindings.handle), final=False).to_dict())
            elif event_type == "done":
                final_answer = str(event.get("content", "") or "").strip() or final_answer.strip()
                usage = event.get("usage")
                if not started and stream_deltas:
                    await bindings.runtime.emit(bindings.handle, "answer.started", AnswerRecord(content="", segment_index=bindings.runtime.current_segment_index(bindings.handle), final=False).to_dict())
                if stream_deltas:
                    await bindings.runtime.emit(bindings.handle, "answer.completed", AnswerRecord(content=final_answer, segment_index=bindings.runtime.current_segment_index(bindings.handle), final=True, input_tokens=int(usage.get("input_tokens", 0) or 0) if isinstance(usage, dict) and usage.get("input_tokens") is not None else None, output_tokens=int(usage.get("output_tokens", 0) or 0) if isinstance(usage, dict) and usage.get("output_tokens") is not None else None).to_dict())
        return final_answer, usage

    async def _emit_final_answer(self, content: str, *, usage: dict[str, int] | None = None) -> None:
        bindings = self._bindings_or_raise()
        await bindings.runtime.emit(bindings.handle, "answer.started", AnswerRecord(content="", segment_index=bindings.runtime.current_segment_index(bindings.handle), final=False).to_dict())
        if content:
            await bindings.runtime.emit(bindings.handle, "answer.delta", AnswerRecord(content=content, segment_index=bindings.runtime.current_segment_index(bindings.handle), final=False).to_dict())
        await bindings.runtime.emit(bindings.handle, "answer.completed", AnswerRecord(content=content, segment_index=bindings.runtime.current_segment_index(bindings.handle), final=True, input_tokens=int(usage.get("input_tokens", 0) or 0) if isinstance(usage, dict) and usage.get("input_tokens") is not None else None, output_tokens=int(usage.get("output_tokens", 0) or 0) if isinstance(usage, dict) and usage.get("output_tokens") is not None else None).to_dict())

    def _explicit_capability_selection(self, message: str, allowed_tools: list[Any]) -> tuple[str, dict[str, str] | None]:
        normalized = str(message or "")
        if not any(pattern.search(normalized) for pattern in (*_EXPLICIT_MCP_PATTERNS, *_EXPLICIT_WEB_MCP_PATTERNS)):
            return "", None
        if any(pattern.search(normalized) for pattern in _REPEATED_MCP_PATTERNS):
            return "", None
        if len(allowed_tools) != 1 or not isinstance(allowed_tools[0], GovernedCapabilityTool):
            return "", None
        tool_name = str(getattr(allowed_tools[0], "name", "") or "")
        if tool_name not in _EXPLICIT_CAPABILITY_IDS:
            return "", None
        patterns = _FETCH_URL_PATTERNS
        key = "url"
        if tool_name == "mcp_filesystem_read_file":
            patterns, key = _READ_PATH_PATTERNS, "path"
        elif tool_name == "mcp_filesystem_list_directory":
            patterns, key = _LIST_PATH_PATTERNS, "path"
        for pattern in patterns:
            match = pattern.search(normalized)
            if match:
                return tool_name, {key: str(match.group(1)).strip().rstrip(".,;:")}
        return "", None

    async def _invoke_tool_path(self, *, state: GraphState, message: str, strategy: "ExecutionStrategy | None", skill_decision: SkillDecision | None, allowed_tools: list[Any]) -> dict[str, Any]:
        bindings = self._bindings_or_raise()
        if strategy is None:
            raise RuntimeError("capability path requires execution strategy")
        assembly = self._context_assembler.assemble(
            path_kind="capability_path",
            state=state,
            call_site="tool_agent",
        )
        extra_instructions = list(assembly.extra_instructions)
        extra_instructions.extend(self._execution.tool_agent_instructions(strategy, skill_decision or SkillDecision(False, "", 0.0, "")))
        agent = self._execution.build_tool_agent(
            extra_instructions=extra_instructions,
            tools_override=allowed_tools,
        )
        messages = list(assembly.history_messages)
        messages.append({"role": "user", "content": message})
        final_parts: list[str] = []
        answer_segments: list[str] = []
        last_ai_message = ""
        last_streamed = ""
        pending_tools: dict[str, dict[str, str]] = {}
        recorded_tools: list[dict[str, str]] = []
        capability_results: list[dict[str, Any]] = []
        answer_started = False

        async for mode, payload in agent.astream({"messages": messages}, stream_mode=["messages", "updates"]):
            if mode == "messages":
                chunk, metadata = payload
                if metadata.get("langgraph_node") != "model":
                    continue
                text = _stringify_content(getattr(chunk, "content", ""))
                next_chunk = self._execution.incremental_stream_text(last_streamed, text)
                if text:
                    last_streamed = text
                if next_chunk:
                    if not answer_started:
                        await bindings.runtime.emit(bindings.handle, "answer.started", AnswerRecord(content="", segment_index=bindings.runtime.current_segment_index(bindings.handle), final=False).to_dict())
                        answer_started = True
                    final_parts.append(next_chunk)
                    answer_segments.append(next_chunk)
                    await bindings.runtime.emit(bindings.handle, "answer.delta", AnswerRecord(content=next_chunk, segment_index=bindings.runtime.current_segment_index(bindings.handle), final=False).to_dict())
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
                            tool_input = tool_call.get("args", "")
                            if not isinstance(tool_input, str):
                                tool_input = json.dumps(tool_input, ensure_ascii=False)
                            pending_tools[call_id] = {"tool": tool_name, "input": str(tool_input)}
                            await bindings.runtime.emit(bindings.handle, "tool.started", ToolCallRecord(tool=tool_name, input=str(tool_input), call_id=call_id).to_dict())
                    if message_type == "tool":
                        tool_call_id = str(getattr(agent_message, "tool_call_id", ""))
                        pending = pending_tools.pop(tool_call_id, {"tool": getattr(agent_message, "name", "tool"), "input": ""})
                        output = _stringify_content(getattr(agent_message, "content", ""))
                        recorded_tools.append({"tool": pending["tool"], "input": pending["input"], "output": output, "call_id": tool_call_id})
                        structured_result = self._consume_captured_result(bindings.context, pending["tool"])
                        if structured_result is None:
                            structured_result = {"capability_id": pending["tool"], "call_id": tool_call_id, "status": "success", "payload": {"text": output}}
                        capability_results.append(structured_result)
                        await bindings.runtime.emit(bindings.handle, "tool.completed", ToolCallRecord(tool=pending["tool"], input=pending["input"], output=output, call_id=tool_call_id).to_dict())
                        bindings.runtime.advance_answer_segment(bindings.handle)
                        answer_started = False

        final_answer = "".join(final_parts).strip() or last_ai_message.strip()
        last_failure = extract_latest_failed_capability({"capability_results": capability_results})
        result = {
            "recorded_tools": recorded_tools,
            "capability_results": capability_results,
            "final_answer": final_answer,
            "answer_segments": answer_segments,
            "needs_answer_synthesis": self._execution.needs_tool_result_fallback(final_answer, recorded_tools),
            "answer_finalized": False,
            "error_state": None,
            "last_failure": last_failure.to_dict() if last_failure is not None else None,
            "recovery_action": "",
            "recovered_from_failure": False,
        }
        if final_answer and not result["needs_answer_synthesis"]:
            self._record_turn_context_snapshot(
                state=state,
                assembly=assembly,
                call_site="tool_agent",
                model_invoked=True,
            )
        updates = self._context_writer.snapshot({**dict(state), **result}, updated_at=bindings.runtime.now())
        return {**result, **updates}

    def _consume_captured_result(self, context: CapabilityRuntimeContext, capability_id: str) -> dict[str, Any] | None:
        for index, entry in enumerate(list(context.result_log)):
            if str(entry.get("capability_id", "") or "") == str(capability_id or ""):
                return context.result_log.pop(index)
        return None

    async def _stream_tool_result_fallback(self, *, state: GraphState, user_message: str, recorded_tools: list[dict[str, str]], strategy: "ExecutionStrategy | None") -> str:
        assembly = self._context_assembler.assemble(
            path_kind="capability_path",
            state=state,
            call_site="tool_result_fallback",
        )
        self._record_turn_context_snapshot(
            state=state,
            assembly=assembly,
            call_site="tool_result_fallback",
            model_invoked=True,
        )
        fallback_messages = list(assembly.history_messages)
        fallback_messages.append({"role": "assistant", "content": self._execution.tool_results_context(recorded_tools)})
        fallback_messages.append({"role": "user", "content": user_message})
        instructions = [
            "The tool calls already succeeded. Do not call more tools.",
            "Answer the user's original request directly using the provided tool results.",
            "Your answer must be natural-language and user-facing, not an internal note.",
        ]
        instructions.extend(assembly.extra_instructions)
        if strategy is not None:
            instructions.extend(strategy.to_instructions())
        answer, _usage = await self._stream_model_answer(fallback_messages, extra_instructions=instructions)
        if answer:
            return answer
        fallback = "Based on the completed capability results, here is the consolidated answer:\n\n" + "\n\n".join(
            str(item.get("output", "")).strip()[:1200] for item in recorded_tools if str(item.get("output", "")).strip()
        )
        await self._emit_final_answer(fallback)
        return fallback

    def _record_turn_context_snapshot(
        self,
        *,
        state: GraphState,
        assembly: ContextAssembly,
        call_site: str,
        model_invoked: bool,
    ) -> None:
        bindings = self._bindings_or_raise()
        thread_id = str(state.get("thread_id", "") or state.get("session_id", "") or "").strip()
        if not thread_id:
            thread_id = self._thread_id_for(bindings.handle)
        run_id = str(state.get("run_id", "") or bindings.handle.run_id).strip()
        session_id = str(state.get("session_id", "") or getattr(bindings.handle.metadata, "session_id", "") or "").strip() or None
        segment_index = bindings.runtime.current_segment_index(bindings.handle)
        turn_id = f"{run_id}:{segment_index}"
        checkpoint_meta = dict(state.get("checkpoint_meta", {}) or {})
        run_status = str(
            checkpoint_meta.get("run_status", "")
            or getattr(bindings.handle.metadata, "run_status", "")
            or ("recovery" if assembly.path_kind == "recovery_path" else "fresh")
        )
        budget_report = {
            "allocated": assembly.budget.to_dict(),
            "used": dict(assembly.budget_used),
            "excluded_from_prompt": list(assembly.excluded_from_prompt),
        }
        snapshot = ContextTurnSnapshot(
            turn_id=turn_id,
            session_id=session_id,
            run_id=run_id,
            thread_id=thread_id,
            assistant_message_id=None,
            segment_index=segment_index,
            call_site=call_site,
            path_type=assembly.path_kind,
            user_query=str(state.get("user_message", "") or ""),
            context_envelope=assembly.envelope,
            assembly_decision=assembly.decision,
            budget_report=budget_report,
            selected_memory_ids=assembly.decision.selected_memory_ids,
            selected_artifact_ids=assembly.decision.selected_artifact_ids,
            selected_evidence_ids=assembly.decision.selected_evidence_ids,
            selected_conversation_ids=assembly.decision.selected_conversation_ids,
            dropped_items=assembly.decision.dropped_items,
            truncation_reason=assembly.decision.truncation_reason,
            run_status=run_status,
            resume_source=str(
                checkpoint_meta.get("resume_source", "")
                or getattr(bindings.handle.metadata, "resume_source", "")
                or self._resume_source
                or ""
            ),
            checkpoint_id=str(
                checkpoint_meta.get("checkpoint_id", "")
                or getattr(bindings.handle.metadata, "checkpoint_id", "")
                or self._resume_checkpoint_id
                or ""
            ),
            orchestration_engine=str(
                checkpoint_meta.get("orchestration_engine", "")
                or getattr(bindings.handle.metadata, "orchestration_engine", "")
                or "langgraph"
            ),
            model_invoked=model_invoked,
            created_at=bindings.runtime.now(),
        )
        try:
            context_store.record_context_turn_snapshot(snapshot)
        except Exception:
            return

    def _memory_retrieve(self, message: str) -> list[dict[str, Any]]:
        from src.backend.runtime import executors as executors_module  # pylint: disable=import-outside-toplevel
        return executors_module.memory_indexer.retrieve(message, top_k=3)

    def _knowledge_astream(self, message: str):
        from src.backend.runtime import executors as executors_module  # pylint: disable=import-outside-toplevel
        return executors_module.knowledge_orchestrator.astream(message)
