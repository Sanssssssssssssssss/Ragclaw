"use client";

import { memo, useMemo, useState } from "react";
import { Blocks, Eye, History, Layers3, RefreshCcw } from "lucide-react";

import { useChatStore, useSessionStore } from "@/lib/store";

function formatTimestamp(value: string) {
  if (!value) return "pending";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function objectToText(value: unknown) {
  if (!value) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function SectionBlock({
  title,
  content,
  emptyLabel = "No content was included for this block."
}: {
  title: string;
  content: string;
  emptyLabel?: string;
}) {
  const [raw, setRaw] = useState(false);
  const trimmed = content.trim();
  const lines = useMemo(() => trimmed.split("\n").filter((line) => line.trim().length > 0), [trimmed]);

  return (
    <section className="pixel-card-soft p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="pixel-label">{title}</div>
        <button className="pixel-button px-3 py-1 text-xs" onClick={() => setRaw((value) => !value)} type="button">
          {raw ? "structured" : "raw"}
        </button>
      </div>
      {!trimmed ? (
        <p className="pixel-note">{emptyLabel}</p>
      ) : raw ? (
        <pre className="mono whitespace-pre-wrap text-sm leading-6 text-[var(--color-ink-soft)]">{trimmed}</pre>
      ) : (
        <div className="space-y-2 text-sm leading-6 text-[var(--color-ink-soft)]">
          {lines.map((line, index) => (
            <p key={`${title}-${index}`} className="whitespace-pre-wrap">
              {line}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

const TurnListItem = memo(function TurnListItem({
  active,
  item,
  onSelect
}: {
  active: boolean;
  item: ReturnType<typeof useChatStore>["contextTurns"][number];
  onSelect: (turnId: string) => void;
}) {
  return (
    <button
      className={`w-full rounded-2xl border px-4 py-3 text-left transition ${
        active
          ? "border-[var(--color-accent)] bg-[color-mix(in_srgb,var(--color-accent)_12%,transparent)]"
          : "border-[var(--color-border)] bg-[var(--color-panel-soft)] hover:border-[var(--color-accent-soft)]"
      }`}
      onClick={() => onSelect(item.turn_id)}
      type="button"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="pixel-tag">{item.path_type}</span>
        <span className="pixel-tag">{item.run_status || "fresh"}</span>
        {item.excluded_from_context ? <span className="pixel-tag">excluded</span> : null}
      </div>
      <p className="mt-3 line-clamp-3 text-sm leading-6 text-[var(--color-ink)]">
        {item.user_query || "No user query captured."}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-[var(--color-ink-soft)]">
        <span>{formatTimestamp(item.created_at)}</span>
        <span>{`calls ${item.call_ids.length}`}</span>
        <span>{`mem ${item.selected_memory_ids.length}`}</span>
      </div>
    </button>
  );
});

const CallListItem = memo(function CallListItem({
  active,
  item,
  onSelect
}: {
  active: boolean;
  item: ReturnType<typeof useChatStore>["contextTurnCalls"][number];
  onSelect: (callId: string) => void;
}) {
  return (
    <button
      className={`w-full rounded-2xl border px-4 py-3 text-left transition ${
        active
          ? "border-[var(--color-accent)] bg-[color-mix(in_srgb,var(--color-accent)_12%,transparent)]"
          : "border-[var(--color-border)] bg-[var(--color-panel-soft)] hover:border-[var(--color-accent-soft)]"
      }`}
      onClick={() => onSelect(item.call_id)}
      type="button"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="pixel-tag">{item.call_type}</span>
        <span className="pixel-tag">{item.call_site}</span>
      </div>
      <p className="mt-3 text-sm leading-6 text-[var(--color-ink-soft)]">{formatTimestamp(item.created_at)}</p>
    </button>
  );
});

export function ContextTracePanel() {
  const {
    contextTurns,
    selectedContextTurn,
    contextTurnCalls,
    selectedContextCall,
    contextTurnsLoading,
    selectContextTurn,
    selectContextCall,
    refreshAssets
  } = useChatStore();
  const { currentSessionId } = useSessionStore();

  const activeTurnId = selectedContextTurn?.turn_id ?? contextTurns[0]?.turn_id ?? null;
  const activeCallId = selectedContextCall?.call_id ?? contextTurnCalls[0]?.call_id ?? null;
  const selectedBudget = selectedContextCall?.budget_report ?? selectedContextTurn?.budget_report ?? { allocated: {}, used: {}, excluded_from_prompt: [] };
  const budgetAllocated = selectedBudget.allocated ?? {};
  const budgetUsed = selectedBudget.used ?? {};
  const excluded = Array.isArray(selectedBudget.excluded_from_prompt) ? selectedBudget.excluded_from_prompt : [];
  const envelope = selectedContextCall?.context_envelope ?? selectedContextTurn?.context_envelope ?? null;
  const selectedMemoryIds = selectedContextCall?.selected_memory_ids ?? selectedContextTurn?.selected_memory_ids ?? [];
  const selectedArtifactIds = selectedContextCall?.selected_artifact_ids ?? selectedContextTurn?.selected_artifact_ids ?? [];
  const selectedEvidenceIds = selectedContextCall?.selected_evidence_ids ?? selectedContextTurn?.selected_evidence_ids ?? [];
  const droppedItems = selectedContextCall?.dropped_items ?? selectedContextTurn?.dropped_items ?? [];
  const truncationReason = selectedContextCall?.truncation_reason ?? selectedContextTurn?.truncation_reason ?? "";

  if (!currentSessionId) {
    return (
      <div className="pixel-card-soft px-6 py-8">
        <p className="pixel-label">context trace</p>
        <p className="pixel-note mt-4">Select a session to inspect model-visible context.</p>
      </div>
    );
  }

  if (!contextTurns.length && !contextTurnsLoading) {
    return (
      <div className="pixel-card-soft px-6 py-8">
        <p className="pixel-label">context trace</p>
        <h3 className="pixel-title mt-3 text-[1rem] text-[var(--color-ink)]">No assistant turn snapshot yet</h3>
        <p className="pixel-note mt-4 max-w-3xl">
          Once this session produces an assistant answer, the model-call level context snapshots will appear here.
        </p>
      </div>
    );
  }

  return (
    <div className="grid min-h-0 gap-4 xl:grid-cols-[260px_260px_minmax(0,1fr)]">
      <aside className="pixel-card-soft min-h-0 p-3">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div className="pixel-label flex items-center gap-2">
            <History size={14} />
            assistant turns
          </div>
          <button className="pixel-button px-3 py-1 text-xs" onClick={() => void refreshAssets()} type="button">
            <RefreshCcw size={12} />
          </button>
        </div>
        <div className="space-y-3 overflow-y-auto pr-1">
          {contextTurns.map((item) => (
            <TurnListItem
              key={item.turn_id}
              active={item.turn_id === activeTurnId}
              item={item}
              onSelect={(turnId) => void selectContextTurn(turnId)}
            />
          ))}
        </div>
      </aside>

      <aside className="pixel-card-soft min-h-0 p-3">
        <div className="mb-3 flex items-center gap-2 pixel-label">
          <Layers3 size={14} />
          model calls
        </div>
        {!contextTurnCalls.length ? (
          <div className="pixel-note px-2 py-3 text-sm">No model call was recorded for this turn.</div>
        ) : (
          <div className="space-y-3 overflow-y-auto pr-1">
            {contextTurnCalls.map((item) => (
              <CallListItem
                key={item.call_id}
                active={item.call_id === activeCallId}
                item={item}
                onSelect={(callId) => void selectContextCall(callId)}
              />
            ))}
          </div>
        )}
      </aside>

      <div className="space-y-4 overflow-y-auto pr-1">
        {contextTurnsLoading && !selectedContextTurn ? (
          <div className="pixel-card-soft px-6 py-8 text-sm text-[var(--color-ink-soft)]">Loading context trace...</div>
        ) : selectedContextTurn ? (
          <>
            <section className="pixel-card p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="ui-pill">
                  <Eye size={14} />
                  model-visible context
                </div>
                <div className="mono text-sm text-[var(--color-ink-soft)]">{selectedContextTurn.turn_id}</div>
              </div>
              <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
                <SectionBlock title="User query" content={selectedContextTurn.user_query} />
                <section className="pixel-card-soft p-4">
                  <div className="pixel-label mb-3 flex items-center gap-2">
                    <Blocks size={14} />
                    Path / run meta
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="pixel-tag">{selectedContextTurn.path_type}</span>
                    <span className="pixel-tag">{selectedContextTurn.run_status || "fresh"}</span>
                    {selectedContextTurn.excluded_from_context ? <span className="pixel-tag">excluded from future context</span> : null}
                    {selectedContextCall ? <span className="pixel-tag">{selectedContextCall.call_type}</span> : <span className="pixel-tag">post-turn state</span>}
                  </div>
                  <div className="mt-4 space-y-2 text-sm leading-6 text-[var(--color-ink-soft)]">
                    <p>{`turn created: ${formatTimestamp(selectedContextTurn.created_at)}`}</p>
                    <p>{`run: ${selectedContextTurn.run_id}`}</p>
                    <p>{`thread: ${selectedContextTurn.thread_id}`}</p>
                    {selectedContextTurn.checkpoint_id ? <p>{`checkpoint: ${selectedContextTurn.checkpoint_id}`}</p> : null}
                    {selectedContextTurn.resume_source ? <p>{`resume source: ${selectedContextTurn.resume_source}`}</p> : null}
                    {selectedContextTurn.excluded_at ? <p>{`excluded at: ${formatTimestamp(selectedContextTurn.excluded_at)}`}</p> : null}
                    {selectedContextTurn.exclusion_reason ? <p>{`exclusion reason: ${selectedContextTurn.exclusion_reason}`}</p> : null}
                  </div>
                </section>
              </div>
            </section>

            <div className="grid gap-4 xl:grid-cols-2">
              <SectionBlock title="System block" content={envelope?.system_block ?? ""} />
              <SectionBlock title="Recent history" content={envelope?.history_block ?? ""} />
              <SectionBlock title="Working memory" content={envelope?.working_memory_block ?? ""} />
              <SectionBlock title="Episodic memory" content={envelope?.episodic_block ?? ""} />
              <SectionBlock title="Semantic memory hits" content={envelope?.semantic_block ?? ""} />
              <SectionBlock title="Procedural memory hits" content={envelope?.procedural_block ?? ""} />
              <SectionBlock title="Conversation recall" content={envelope?.conversation_block ?? ""} />
              <SectionBlock title="Artifacts / MCP / capability outputs" content={envelope?.artifact_block ?? ""} />
              <SectionBlock title="Retrieval evidence" content={envelope?.evidence_block ?? ""} />
              <SectionBlock
                title="Post-turn state snapshot"
                content={objectToText(selectedContextTurn.post_turn_state_snapshot)}
                emptyLabel="No post-turn state snapshot was recorded."
              />
            </div>

            <div className="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
              <section className="pixel-card-soft p-4">
                <div className="pixel-label mb-3">Budget / token allocation</div>
                <div className="space-y-3 text-sm text-[var(--color-ink-soft)]">
                  {Object.keys(budgetAllocated).length ? (
                    <div>
                      <p className="pixel-label mb-2">allocated</p>
                      <div className="space-y-1">
                        {Object.entries(budgetAllocated).map(([key, value]) => (
                          <p key={`allocated-${key}`}>{`${key}: ${value}`}</p>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <p className="pixel-note">No budget allocation metadata recorded.</p>
                  )}
                  {Object.keys(budgetUsed).length ? (
                    <div>
                      <p className="pixel-label mb-2">used</p>
                      <div className="space-y-1">
                        {Object.entries(budgetUsed).map(([key, value]) => (
                          <p key={`used-${key}`}>{`${key}: ${value}`}</p>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {excluded.length ? (
                    <div>
                      <p className="pixel-label mb-2">never injected</p>
                      <div className="space-y-1">
                        {excluded.map((item) => (
                          <p key={item}>{item}</p>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              </section>

              <section className="pixel-card-soft p-4">
                <div className="pixel-label mb-3">Selected / dropped items</div>
                <div className="space-y-3 text-sm text-[var(--color-ink-soft)]">
                  <div>
                    <p className="pixel-label mb-2">selected memory ids</p>
                    {selectedMemoryIds.length ? (
                      selectedMemoryIds.map((item) => <p key={item}>{item}</p>)
                    ) : (
                      <p className="pixel-note">No governed memories were selected.</p>
                    )}
                  </div>
                  <div>
                    <p className="pixel-label mb-2">selected artifact ids</p>
                    {selectedArtifactIds.length ? (
                      selectedArtifactIds.map((item) => <p key={item}>{item}</p>)
                    ) : (
                      <p className="pixel-note">No artifacts were selected.</p>
                    )}
                  </div>
                  <div>
                    <p className="pixel-label mb-2">selected evidence ids</p>
                    {selectedEvidenceIds.length ? (
                      selectedEvidenceIds.map((item) => <p key={item}>{item}</p>)
                    ) : (
                      <p className="pixel-note">No retrieval evidence ids were recorded.</p>
                    )}
                  </div>
                  <div>
                    <p className="pixel-label mb-2">dropped items / truncation</p>
                    {droppedItems.length ? (
                      droppedItems.map((item) => <p key={item}>{item}</p>)
                    ) : (
                      <p className="pixel-note">No items were dropped from this snapshot.</p>
                    )}
                    {truncationReason ? <p className="mt-2 text-[var(--color-ink)]">{truncationReason}</p> : null}
                  </div>
                </div>
              </section>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
