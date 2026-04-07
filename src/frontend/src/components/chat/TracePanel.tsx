"use client";

import { memo, useMemo } from "react";
import { MessageSquareText, Route, Sparkles } from "lucide-react";

import { RetrievalCard } from "@/components/chat/RetrievalCard";
import { ThoughtChain } from "@/components/chat/ThoughtChain";
import { VirtualizedStack } from "@/components/chat/VirtualizedStack";
import { useChatStore, useSessionStore } from "@/lib/store";

const TRACE_ITEM_ESTIMATE = 760;

type TraceTurn = {
  id: string;
  prompt: string | null;
  answer: string;
  toolCalls: ReturnType<typeof useChatStore>["messages"][number]["toolCalls"];
  retrievalSteps: ReturnType<typeof useChatStore>["messages"][number]["retrievalSteps"];
  usage: ReturnType<typeof useChatStore>["messages"][number]["usage"];
  runMeta: ReturnType<typeof useChatStore>["messages"][number]["runMeta"];
  checkpointEvents: ReturnType<typeof useChatStore>["messages"][number]["checkpointEvents"];
  streaming: boolean;
};

const TraceTurnCard = memo(function TraceTurnCard({ turn }: { turn: TraceTurn }) {
  const hasTrace =
    turn.toolCalls.length > 0 || turn.retrievalSteps.length > 0 || turn.checkpointEvents.length > 0;

  return (
    <article className="rounded-[28px] border border-[var(--color-line)] bg-[rgba(255,255,255,0.03)] p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="ui-pill">
          <Route size={14} />
          {turn.streaming ? "Live turn trace" : "Turn trace"}
        </div>
        {turn.usage ? (
          <div className="mono text-sm text-[var(--color-ink-soft)]">
            {`${turn.usage.input_tokens.toLocaleString()} in / ${turn.usage.output_tokens.toLocaleString()} out`}
          </div>
        ) : null}
      </div>

      {turn.runMeta ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs uppercase tracking-[0.18em] text-[var(--color-ink-muted)]">
          <span className="rounded-full border border-[var(--color-line)] px-2 py-1">
            {turn.runMeta.status}
          </span>
          <span className="rounded-full border border-[var(--color-line)] px-2 py-1">
            {turn.runMeta.orchestration_engine || "langgraph"}
          </span>
          {turn.runMeta.thread_id ? (
            <span className="mono normal-case tracking-normal text-[var(--color-ink-soft)]">
              thread {turn.runMeta.thread_id}
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <section className="rounded-[24px] border border-[var(--color-line)] bg-[rgba(255,255,255,0.02)] p-4">
          <div className="mb-2 flex items-center gap-2 text-sm uppercase tracking-[0.18em] text-[var(--color-ink-muted)]">
            <MessageSquareText size={14} />
            User prompt
          </div>
          <div className="whitespace-pre-wrap text-[0.98rem] leading-7 text-white">
            {turn.prompt?.trim() || "No direct user prompt captured for this assistant turn."}
          </div>
        </section>

        <section className="rounded-[24px] border border-[var(--color-line)] bg-[rgba(255,255,255,0.02)] p-4">
          <div className="mb-2 flex items-center gap-2 text-sm uppercase tracking-[0.18em] text-[var(--color-ink-muted)]">
            <Sparkles size={14} />
            Assistant response
          </div>
          <div className="whitespace-pre-wrap text-[0.98rem] leading-7 text-[var(--color-ink-soft)]">
            {turn.answer?.trim() || (turn.streaming ? "Streaming response..." : "No text response.")}
          </div>
        </section>
      </div>

      <div className="mt-4">
        {turn.checkpointEvents.length ? (
          <div className="mb-4 rounded-[22px] border border-[var(--color-line)] bg-[rgba(255,255,255,0.02)] px-4 py-3 text-sm text-[var(--color-ink-soft)]">
            <p className="mb-2 text-xs uppercase tracking-[0.18em] text-[var(--color-ink-muted)]">
              Checkpoint trace
            </p>
            <div className="space-y-2">
              {turn.checkpointEvents.map((item, index) => (
                <div key={`${item.type}-${item.checkpoint_id}-${index}`} className="flex flex-wrap items-center gap-2">
                  <span className="rounded-full border border-[var(--color-line)] px-2 py-1 uppercase">
                    {item.type}
                  </span>
                  <span className="mono text-[12px] text-[var(--color-ink-soft)]">
                    {item.checkpoint_id || "pending checkpoint"}
                  </span>
                  {item.type === "interrupted" ? <span>Interrupted before resume</span> : null}
                  {item.type === "resumed" ? <span>Resumed from checkpoint</span> : null}
                  {item.type === "created" ? <span>Checkpoint created</span> : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}
        {turn.retrievalSteps.length ? <RetrievalCard steps={turn.retrievalSteps} /> : null}
        {turn.toolCalls.length ? <ThoughtChain toolCalls={turn.toolCalls} /> : null}
        {!hasTrace ? (
          <div className="rounded-[22px] border border-dashed border-[var(--color-line)] bg-[rgba(255,255,255,0.02)] px-4 py-3 text-sm text-[var(--color-ink-soft)]">
            No retrieval or tool trace was emitted for this turn.
          </div>
        ) : null}
      </div>
    </article>
  );
});

/**
 * Returns one rendered trace page from chat-store inputs and isolates per-turn traces away from the main chat view.
 */
export function TracePanel() {
  const { messages, streamingMessages, isStreaming, tokenStats, checkpoints, resumeCheckpoint } = useChatStore();
  const { currentSessionId } = useSessionStore();
  const resumableCheckpoint = useMemo(
    () => checkpoints.find((item) => item.resume_eligible) ?? null,
    [checkpoints]
  );

  const turns = useMemo(() => {
    const combined = [...messages, ...streamingMessages];
    const nextTurns: TraceTurn[] = [];
    let lastUserPrompt: string | null = null;

    for (const message of combined) {
      if (message.role === "user") {
        lastUserPrompt = message.content;
        continue;
      }

      nextTurns.push({
        id: message.id,
        prompt: lastUserPrompt,
        answer: message.content,
        toolCalls: message.toolCalls,
        retrievalSteps: message.retrievalSteps,
        usage: message.usage,
        runMeta: message.runMeta,
        checkpointEvents: message.checkpointEvents,
        streaming: isStreaming && streamingMessages.some((item) => item.id === message.id)
      });
    }

    return nextTurns.reverse();
  }, [isStreaming, messages, streamingMessages]);

  return (
    <section className="flex h-full min-w-0 flex-[1.5] flex-col gap-3 px-1">
      <div className="panel flex items-center justify-between rounded-[28px] px-5 py-4">
        <div>
          <p className="text-xs uppercase tracking-[0.32em] text-[var(--color-ink-muted)]">
            Trace explorer
          </p>
          <h2 className="text-[1.45rem] font-semibold tracking-[-0.04em] text-white">
            One page just for retrieval and tool traces
          </h2>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3">
          {currentSessionId && resumableCheckpoint ? (
            <button
              className="ui-button"
              disabled={isStreaming}
              onClick={() => void resumeCheckpoint(resumableCheckpoint.checkpoint_id)}
              type="button"
            >
              {isStreaming ? "Restoring..." : "Resume Latest Checkpoint"}
            </button>
          ) : null}
          <div className="mono rounded-[20px] border border-[var(--color-line)] bg-[var(--color-bg-soft)] px-4 py-2 text-right text-sm text-[var(--color-ink-soft)]">
            {tokenStats ? (
              <div className="space-y-1">
                <div>{`Model call ${tokenStats.model_call_total_tokens.toLocaleString()} tokens`}</div>
                <div className="text-[var(--color-ink-muted)]">{`Session trace ${tokenStats.session_trace_tokens.toLocaleString()} tokens`}</div>
              </div>
            ) : (
              "No metrics yet"
            )}
          </div>
        </div>
      </div>

      <div className="panel flex min-h-0 flex-1 flex-col rounded-[30px] px-5 pb-4 pt-5">
        {!turns.length ? (
          <div className="trace-scroll-area flex-1 overflow-y-auto pr-2">
            <div className="rounded-[30px] border border-dashed border-[var(--color-line)] bg-[var(--color-bg-soft)] px-7 py-8">
              <p className="text-xs uppercase tracking-[0.34em] text-[var(--color-ink-muted)]">
                Ready
              </p>
              <h3 className="mt-3 max-w-3xl text-[2.4rem] font-semibold tracking-[-0.06em] text-white">
                Trace will appear here turn by turn
              </h3>
              <p className="mt-4 max-w-3xl text-lg leading-8 text-[var(--color-ink-soft)]">
                Switch back to Chat to talk normally. This page keeps every retrieval step and tool call separate from the main conversation so the primary chat stays lighter and cleaner.
              </p>
            </div>
          </div>
        ) : (
          <VirtualizedStack
            className="trace-scroll-area flex-1 overflow-y-auto pr-2"
            estimateHeight={TRACE_ITEM_ESTIMATE}
            getKey={(turn) => turn.id}
            items={turns}
            renderItem={(turn) => (
              <div className="pb-5">
                <TraceTurnCard turn={turn} />
              </div>
            )}
          />
        )}
      </div>
    </section>
  );
}
