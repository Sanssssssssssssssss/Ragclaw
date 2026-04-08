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
    assetsLoading,
    isStreaming,
    refreshAssets,
    resumeCheckpoint,
    submitHitlDecision
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
              Checkpoints, HITL requests, and MCP capabilities
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
