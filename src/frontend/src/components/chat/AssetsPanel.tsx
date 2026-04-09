"use client";

import { useEffect, useMemo, useState } from "react";

import { useChatStore, useSessionStore } from "@/lib/store";

function prettyJson(value: Record<string, unknown> | null | undefined) {
  return JSON.stringify(value ?? {}, null, 2);
}

export function AssetsPanel() {
  const {
    checkpoints,
    pendingHitl,
    hitlAudit,
    mcpCapabilities,
    sessionContext,
    selectedContextTurn,
    derivedTurnMemories,
    assetsLoading,
    isStreaming,
    refreshAssets,
    triggerConsolidation,
    resumeCheckpoint,
    submitHitlDecision,
    excludeContextTurn
  } = useChatStore();
  const { currentSessionId } = useSessionStore();
  const [editedInputText, setEditedInputText] = useState("{}");
  const [editError, setEditError] = useState("");

  useEffect(() => {
    setEditedInputText(prettyJson(pendingHitl?.proposed_input));
    setEditError("");
  }, [pendingHitl]);

  const latestCheckpoint = useMemo(
    () => checkpoints.find((item) => item.resume_eligible) ?? null,
    [checkpoints]
  );

  const handleEditAndContinue = async () => {
    if (!pendingHitl) return;
    try {
      const parsed = JSON.parse(editedInputText) as Record<string, unknown>;
      setEditError("");
      await submitHitlDecision(pendingHitl.checkpoint_id, "edit", parsed);
    } catch (error) {
      setEditError(error instanceof Error ? error.message : "Invalid JSON input");
    }
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="panel flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4 pt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="pixel-label">assets</p>
            <h3 className="pixel-title mt-2 text-[1rem] text-[var(--color-ink)]">
              Checkpoints, context memory, HITL requests, and MCP capabilities
            </h3>
          </div>
          <button className="ui-button" disabled={assetsLoading || isStreaming} onClick={() => void refreshAssets()} type="button">
            {assetsLoading ? "Refreshing..." : "Refresh"}
          </button>
        </div>

        {!currentSessionId ? (
          <div className="pixel-card-soft px-4 py-4 text-sm text-[var(--color-ink-soft)]">
            No active session yet.
          </div>
        ) : null}

        <section className="pixel-card p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="pixel-label">checkpoints</p>
              <p className="pixel-note mt-2">Resume from an existing thread checkpoint.</p>
            </div>
            {latestCheckpoint ? (
              <button
                className="ui-button"
                disabled={isStreaming}
                onClick={() => void resumeCheckpoint(latestCheckpoint.checkpoint_id)}
                type="button"
              >
                Resume latest
              </button>
            ) : null}
          </div>
          <div className="mt-4 space-y-3">
            {checkpoints.length ? (
              checkpoints.map((item) => (
                <div className="pixel-card-soft p-4" key={item.checkpoint_id}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="pixel-tag">{item.state_label}</span>
                    {item.is_latest ? <span className="pixel-tag">latest</span> : null}
                    <span className="mono text-[0.92rem] text-[var(--color-ink-soft)]">{item.checkpoint_id}</span>
                  </div>
                  <div className="mt-3 grid gap-2 text-sm text-[var(--color-ink-soft)] md:grid-cols-2">
                    <p>thread_id: {item.thread_id}</p>
                    <p>session_id: {item.session_id || "-"}</p>
                    <p>run_id: {item.run_id}</p>
                    <p>created_at: {item.created_at || "-"}</p>
                    <p>resumed_from: {item.source || "-"}</p>
                    <p>current status: {item.state_label}</p>
                  </div>
                  {item.resume_eligible ? (
                    <div className="mt-3">
                      <button
                        className="ui-button"
                        disabled={isStreaming}
                        onClick={() => void resumeCheckpoint(item.checkpoint_id)}
                        type="button"
                      >
                        Resume this checkpoint
                      </button>
                    </div>
                  ) : null}
                </div>
              ))
            ) : (
              <div className="pixel-card-soft px-4 py-4 text-sm text-[var(--color-ink-soft)]">
                No checkpoints for this session yet.
              </div>
            )}
          </div>
        </section>

        <section className="pixel-card p-4">
          <p className="pixel-label">hitl</p>
          <p className="pixel-note mt-2">Pending approval plus the latest request and decision audit trail.</p>
          {pendingHitl ? (
            <div className="pixel-card-soft mt-4 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="pixel-tag">pending</span>
                <span className="pixel-tag">risk {pendingHitl.risk_level}</span>
                <span className="mono text-[0.92rem] text-[var(--color-ink-soft)]">
                  request {pendingHitl.request_id || "-"}
                </span>
              </div>
              <h4 className="pixel-title mt-3 text-[1rem] text-[var(--color-ink)]">{pendingHitl.display_name}</h4>
              <p className="pixel-note mt-2">{pendingHitl.reason}</p>
              <div className="mt-3 grid gap-2 text-sm text-[var(--color-ink-soft)] md:grid-cols-2">
                <p>checkpoint_id: {pendingHitl.checkpoint_id}</p>
                <p>requested_at: {pendingHitl.requested_at || "-"}</p>
              </div>
              <label className="pixel-label mt-4 block">edited payload</label>
              <textarea
                className="mt-2 min-h-[170px] w-full rounded-[8px] border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-3 font-mono text-sm text-[var(--color-ink)] outline-none"
                onChange={(event) => setEditedInputText(event.target.value)}
                value={editedInputText}
              />
              {editError ? <p className="mt-2 text-sm text-[var(--color-danger)]">{editError}</p> : null}
              <div className="mt-4 flex flex-wrap gap-3">
                <button
                  className="ui-button ui-button-primary"
                  disabled={isStreaming}
                  onClick={() => void submitHitlDecision(pendingHitl.checkpoint_id, "approve")}
                  type="button"
                >
                  Approve
                </button>
                <button
                  className="ui-button"
                  disabled={isStreaming}
                  onClick={() => void handleEditAndContinue()}
                  type="button"
                >
                  Edit and continue
                </button>
                <button
                  className="ui-button"
                  disabled={isStreaming}
                  onClick={() => void submitHitlDecision(pendingHitl.checkpoint_id, "reject")}
                  type="button"
                >
                  Reject
                </button>
              </div>
            </div>
          ) : (
            <div className="pixel-card-soft mt-4 px-4 py-4 text-sm text-[var(--color-ink-soft)]">
              No pending HITL request right now.
            </div>
          )}
          <div className="mt-4 space-y-3">
            {hitlAudit.length ? (
              hitlAudit.map((entry) => (
                <div className="pixel-card-soft p-4" key={entry.request.request_id || entry.request.checkpoint_id}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="pixel-tag">{entry.request.status || "pending"}</span>
                    <span className="pixel-tag">{entry.request.capability_id}</span>
                    <span className="mono text-[0.92rem] text-[var(--color-ink-soft)]">
                      checkpoint {entry.request.checkpoint_id}
                    </span>
                  </div>
                  <div className="mt-3 grid gap-2 text-sm text-[var(--color-ink-soft)] md:grid-cols-2">
                    <p>request_id: {entry.request.request_id || "-"}</p>
                    <p>risk_level: {entry.request.risk_level}</p>
                    <p>requested_at: {entry.request.requested_at || "-"}</p>
                    <p>decision_id: {entry.decision?.decision_id || "-"}</p>
                    <p>decision: {entry.decision?.decision || "-"}</p>
                    <p>actor: {entry.decision?.actor_id || "-"}</p>
                  </div>
                </div>
              ))
            ) : null}
          </div>
        </section>

        <section className="pixel-card p-4">
          <p className="pixel-label">context memory</p>
          <p className="pixel-note mt-2">Current working memory, episodic summary, and the latest semantic/procedural hits used for context assembly.</p>
          {sessionContext ? (
            <div className="mt-4 space-y-3">
              <div className="pixel-card-soft p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="pixel-tag">working memory</span>
                  <span className="mono text-[0.92rem] text-[var(--color-ink-soft)]">{sessionContext.thread_id}</span>
                </div>
                <div className="mt-3 grid gap-2 text-sm text-[var(--color-ink-soft)] md:grid-cols-2">
                  <p>current_goal: {sessionContext.working_memory.current_goal || "-"}</p>
                  <p>latest_user_intent: {sessionContext.working_memory.latest_user_intent || "-"}</p>
                  <p>active_constraints: {(sessionContext.working_memory.active_constraints || []).join(" | ") || "-"}</p>
                  <p>active_entities: {(sessionContext.working_memory.active_entities || []).join(" | ") || "-"}</p>
                  <p>active_artifacts: {(sessionContext.working_memory.active_artifacts || []).join(" | ") || "-"}</p>
                  <p>unresolved_items: {(sessionContext.working_memory.unresolved_items || []).join(" | ") || "-"}</p>
                </div>
              </div>

              <div className="pixel-card-soft p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="pixel-tag">episodic summary</span>
                  <span className="pixel-tag">v{sessionContext.episodic_summary.summary_version}</span>
                </div>
                <div className="mt-3 grid gap-2 text-sm text-[var(--color-ink-soft)] md:grid-cols-2">
                  <p>key_facts: {(sessionContext.episodic_summary.key_facts || []).join(" | ") || "-"}</p>
                  <p>completed_subtasks: {(sessionContext.episodic_summary.completed_subtasks || []).join(" | ") || "-"}</p>
                  <p>rejected_paths: {(sessionContext.episodic_summary.rejected_paths || []).join(" | ") || "-"}</p>
                  <p>open_loops: {(sessionContext.episodic_summary.open_loops || []).join(" | ") || "-"}</p>
                </div>
              </div>

              <div className="grid gap-3 xl:grid-cols-2">
                <div className="pixel-card-soft p-4">
                  <p className="pixel-label">semantic memory hits</p>
                  <div className="mt-3 space-y-3">
                    {sessionContext.semantic_memories.length ? (
                      sessionContext.semantic_memories.map((item) => (
                        <div key={item.memory_id} className="rounded-[10px] border border-[var(--color-line)] px-3 py-3 text-sm text-[var(--color-ink-soft)]">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="pixel-tag">{item.namespace}</span>
                            <span className="mono text-[0.92rem]">{item.memory_id}</span>
                          </div>
                          <p className="mt-2 text-[var(--color-ink)]">{item.title}</p>
                          <p className="mt-2">{item.summary || item.content}</p>
                        </div>
                      ))
                    ) : (
                      <p className="text-sm text-[var(--color-ink-soft)]">No semantic memory hits yet.</p>
                    )}
                  </div>
                </div>
                <div className="pixel-card-soft p-4">
                  <p className="pixel-label">procedural memory hits</p>
                  <div className="mt-3 space-y-3">
                    {sessionContext.procedural_memories.length ? (
                      sessionContext.procedural_memories.map((item) => (
                        <div key={item.memory_id} className="rounded-[10px] border border-[var(--color-line)] px-3 py-3 text-sm text-[var(--color-ink-soft)]">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="pixel-tag">{item.namespace}</span>
                            <span className="mono text-[0.92rem]">{item.memory_id}</span>
                          </div>
                          <p className="mt-2 text-[var(--color-ink)]">{item.title}</p>
                          <p className="mt-2">{item.summary || item.content}</p>
                        </div>
                      ))
                    ) : (
                      <p className="text-sm text-[var(--color-ink-soft)]">No procedural memory hits yet.</p>
                    )}
                  </div>
                </div>
              </div>

              <div className="pixel-card-soft p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="pixel-label">memory governance</p>
                    <p className="pixel-note mt-2">Manifest-first recall state, consolidation summary, and lifecycle flags.</p>
                  </div>
                  <button
                    className="ui-button"
                    disabled={assetsLoading || isStreaming}
                    onClick={() => void triggerConsolidation()}
                    type="button"
                  >
                    Run consolidation
                  </button>
                </div>

                <div className="mt-4 grid gap-3 xl:grid-cols-2">
                  <div className="rounded-[10px] border border-[var(--color-line)] px-3 py-3 text-sm text-[var(--color-ink-soft)]">
                    <p className="pixel-label">latest consolidation</p>
                    {sessionContext.latest_consolidation ? (
                      <>
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          <span className="pixel-tag">{sessionContext.latest_consolidation.status}</span>
                          <span className="pixel-tag">{sessionContext.latest_consolidation.trigger}</span>
                          <span className="mono text-[0.92rem]">{sessionContext.latest_consolidation.created_at}</span>
                        </div>
                        <p className="mt-3">
                          promoted: {sessionContext.latest_consolidation.promoted_memory_ids.length} | stale: {sessionContext.latest_consolidation.stale_memory_ids.length} | superseded: {sessionContext.latest_consolidation.superseded_memory_ids.length} | conflicts: {sessionContext.latest_consolidation.conflict_memory_ids.length}
                        </p>
                        <p className="mt-2">{(sessionContext.latest_consolidation.notes || []).join(" | ") || "-"}</p>
                      </>
                    ) : (
                      <p className="mt-3">No consolidation run recorded yet.</p>
                    )}
                  </div>

                  <div className="rounded-[10px] border border-[var(--color-line)] px-3 py-3 text-sm text-[var(--color-ink-soft)]">
                    <p className="pixel-label">conversation recall</p>
                    <div className="mt-3 space-y-2">
                      {sessionContext.conversation_recall.length ? (
                        sessionContext.conversation_recall.slice(0, 4).map((item) => (
                          <div key={item.chunk_id} className="rounded-[8px] border border-[var(--color-line)] px-3 py-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="pixel-tag">{item.role}</span>
                              <span className="mono text-[0.92rem]">{item.updated_at}</span>
                            </div>
                            <p className="mt-2">{item.summary || item.snippet}</p>
                          </div>
                        ))
                      ) : (
                        <p>No thread recall chunks yet.</p>
                      )}
                    </div>
                  </div>
                </div>

                <div className="mt-4 grid gap-3 xl:grid-cols-2">
                  <div className="rounded-[10px] border border-[var(--color-line)] px-3 py-3 text-sm text-[var(--color-ink-soft)]">
                    <p className="pixel-label">episodic memories</p>
                    <div className="mt-3 space-y-3">
                      {sessionContext.episodic_memories.length ? (
                        sessionContext.episodic_memories.map((item) => (
                          <div key={item.memory_id} className="rounded-[8px] border border-[var(--color-line)] px-3 py-3">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="pixel-tag">{item.status || "active"}</span>
                              <span className="pixel-tag">{item.freshness || "fresh"}</span>
                              <span className="mono text-[0.92rem]">{item.memory_id}</span>
                            </div>
                            <p className="mt-2 text-[var(--color-ink)]">{item.title}</p>
                            <p className="mt-2">{item.summary || item.content}</p>
                          </div>
                        ))
                      ) : (
                        <p>No episodic memories yet.</p>
                      )}
                    </div>
                  </div>

                  <div className="rounded-[10px] border border-[var(--color-line)] px-3 py-3 text-sm text-[var(--color-ink-soft)]">
                    <p className="pixel-label">memory manifests</p>
                    <div className="mt-3 space-y-3">
                      {sessionContext.manifests.length ? (
                        sessionContext.manifests.slice(0, 10).map((item) => (
                          <div key={item.memory_id} className="rounded-[8px] border border-[var(--color-line)] px-3 py-3">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="pixel-tag">{item.memory_type || item.kind}</span>
                              <span className="pixel-tag">{item.scope || item.namespace}</span>
                              {item.status ? <span className="pixel-tag">{item.status}</span> : null}
                              {item.freshness ? <span className="pixel-tag">{item.freshness}</span> : null}
                              {item.conflict_flag ? <span className="pixel-tag">conflict</span> : null}
                              <span className="mono text-[0.92rem]">{item.memory_id}</span>
                            </div>
                            <p className="mt-2 text-[var(--color-ink)]">{item.title}</p>
                            <p className="mt-2">{item.summary || item.content}</p>
                          </div>
                        ))
                      ) : (
                        <p>No governed manifests yet.</p>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              <div className="pixel-card-soft p-4">
                <p className="pixel-label">recent context assemblies</p>
                <div className="mt-3 space-y-3">
                  {sessionContext.assemblies.length ? (
                    sessionContext.assemblies.map((item) => (
                      <div key={item.assembly_id} className="rounded-[10px] border border-[var(--color-line)] px-3 py-3 text-sm text-[var(--color-ink-soft)]">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="pixel-tag">{item.path_kind}</span>
                          <span className="pixel-tag">{item.call_site}</span>
                          <span className="mono text-[0.92rem]">{item.created_at}</span>
                        </div>
                        <p className="mt-2">
                          selected_memory_ids: {Array.isArray(item.decision?.["selected_memory_ids"]) ? (item.decision["selected_memory_ids"] as string[]).join(" | ") || "-" : "-"}
                        </p>
                        <p className="mt-1">
                          truncation_reason: {typeof item.decision?.["truncation_reason"] === "string" ? (item.decision["truncation_reason"] as string) || "-" : "-"}
                        </p>
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-[var(--color-ink-soft)]">No context assemblies recorded yet.</p>
                  )}
                </div>
              </div>

              <div className="pixel-card-soft p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="pixel-label">context quarantine</p>
                    <p className="pixel-note mt-2">Exclude one polluted turn from future context without deleting the raw session history.</p>
                  </div>
                  {selectedContextTurn ? (
                    <button
                      className="ui-button"
                      disabled={assetsLoading || isStreaming || selectedContextTurn.excluded_from_context}
                      onClick={() => void excludeContextTurn(selectedContextTurn.turn_id)}
                      type="button"
                    >
                      {selectedContextTurn.excluded_from_context ? "Already excluded" : "Exclude from future context"}
                    </button>
                  ) : null}
                </div>
                {selectedContextTurn ? (
                  <div className="mt-4 space-y-3 text-sm text-[var(--color-ink-soft)]">
                    <div className="rounded-[10px] border border-[var(--color-line)] px-3 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="pixel-tag">{selectedContextTurn.path_type}</span>
                        <span className="pixel-tag">{selectedContextTurn.run_status || "fresh"}</span>
                        {selectedContextTurn.excluded_from_context ? <span className="pixel-tag">excluded</span> : null}
                        <span className="mono text-[0.92rem]">{selectedContextTurn.turn_id}</span>
                      </div>
                      <p className="mt-2 text-[var(--color-ink)]">{selectedContextTurn.user_query || "No query captured."}</p>
                      {selectedContextTurn.exclusion_reason ? <p className="mt-2">reason: {selectedContextTurn.exclusion_reason}</p> : null}
                    </div>

                    <div className="grid gap-3 xl:grid-cols-2">
                      <div className="rounded-[10px] border border-[var(--color-line)] px-3 py-3">
                        <p className="pixel-label">derived memories</p>
                        <div className="mt-3 space-y-2">
                          {derivedTurnMemories?.memories.length ? (
                            derivedTurnMemories.memories.map((item) => (
                              <div key={item.memory_id} className="rounded-[8px] border border-[var(--color-line)] px-3 py-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="pixel-tag">{item.memory_type || item.kind}</span>
                                  {item.status ? <span className="pixel-tag">{item.status}</span> : null}
                                  {item.freshness ? <span className="pixel-tag">{item.freshness}</span> : null}
                                  <span className="mono text-[0.92rem]">{item.memory_id}</span>
                                </div>
                                <p className="mt-2 text-[var(--color-ink)]">{item.title}</p>
                                <p className="mt-2">{item.summary || item.content}</p>
                              </div>
                            ))
                          ) : (
                            <p>No derived governed memory found for the selected turn.</p>
                          )}
                        </div>
                      </div>

                      <div className="rounded-[10px] border border-[var(--color-line)] px-3 py-3">
                        <p className="pixel-label">derived conversation recall</p>
                        <div className="mt-3 space-y-2">
                          {derivedTurnMemories?.conversation_recall.length ? (
                            derivedTurnMemories.conversation_recall.map((item) => (
                              <div key={item.chunk_id} className="rounded-[8px] border border-[var(--color-line)] px-3 py-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="pixel-tag">{item.role}</span>
                                  <span className="pixel-tag">{item.status}</span>
                                  <span className="mono text-[0.92rem]">{item.chunk_id}</span>
                                </div>
                                <p className="mt-2">{item.summary || item.snippet}</p>
                              </div>
                            ))
                          ) : (
                            <p>No derived conversation recall entries found for the selected turn.</p>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="mt-4 rounded-[10px] border border-[var(--color-line)] px-3 py-3 text-sm text-[var(--color-ink-soft)]">
                    Pick a turn in the Context Trace panel to inspect derived memories and exclude it from future context.
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="pixel-card-soft mt-4 px-4 py-4 text-sm text-[var(--color-ink-soft)]">
              No context snapshot for this session yet.
            </div>
          )}
        </section>

        <section className="pixel-card p-4">
          <p className="pixel-label">mcp capabilities</p>
          <p className="pixel-note mt-2">Current read-only MCP assets registered in the unified capability system.</p>
          <div className="mt-4 space-y-3">
            {mcpCapabilities.length ? (
              mcpCapabilities.map((item) => (
                <div className="pixel-card-soft p-4" key={item.capability_id}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="pixel-tag">{item.capability_type}</span>
                    <span className="pixel-tag">{item.enabled ? "enabled" : "disabled"}</span>
                    <span className="pixel-tag">risk {item.risk_level}</span>
                  </div>
                  <h4 className="pixel-title mt-3 text-[1rem] text-[var(--color-ink)]">{item.display_name}</h4>
                  <p className="pixel-note mt-2">{item.description}</p>
                  <div className="mt-3 grid gap-2 text-sm text-[var(--color-ink-soft)] md:grid-cols-2">
                    <p>capability_id: {item.capability_id}</p>
                    <p>approval_required: {String(item.approval_required)}</p>
                    <p>timeout_seconds: {item.timeout_seconds}</p>
                    <p>repeated_call_limit: {item.repeated_call_limit}</p>
                  </div>
                </div>
              ))
            ) : (
              <div className="pixel-card-soft px-4 py-4 text-sm text-[var(--color-ink-soft)]">
                No MCP capabilities are registered.
              </div>
            )}
          </div>
        </section>
      </div>
    </section>
  );
}
