"use client";

import { memo, useMemo, useRef, useState } from "react";
import { MessageSquareText, Route, Sparkles } from "lucide-react";

import { ContextTracePanel } from "@/components/chat/ContextTracePanel";
import { RetrievalCard } from "@/components/chat/RetrievalCard";
import { ThoughtChain } from "@/components/chat/ThoughtChain";
import { VirtualizedStack } from "@/components/chat/VirtualizedStack";
import { useChatStore } from "@/lib/store";

const TRACE_ITEM_ESTIMATE = 720;

type TraceTurn = {
  id: string;
  prompt: string | null;
  answer: string;
  toolCalls: ReturnType<typeof useChatStore>["messages"][number]["toolCalls"];
  retrievalSteps: ReturnType<typeof useChatStore>["messages"][number]["retrievalSteps"];
  usage: ReturnType<typeof useChatStore>["messages"][number]["usage"];
  runMeta: ReturnType<typeof useChatStore>["messages"][number]["runMeta"];
  checkpointEvents: ReturnType<typeof useChatStore>["messages"][number]["checkpointEvents"];
  hitlEvents: ReturnType<typeof useChatStore>["messages"][number]["hitlEvents"];
  streaming: boolean;
};

const TraceTurnCard = memo(function TraceTurnCard({ turn }: { turn: TraceTurn }) {
  const hasTrace =
    turn.toolCalls.length > 0 ||
    turn.retrievalSteps.length > 0 ||
    turn.checkpointEvents.length > 0 ||
    turn.hitlEvents.length > 0;

  return (
    <article className="pixel-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="ui-pill">
          <Route size={14} />
          {turn.streaming ? "Live trace" : "Trace"}
        </div>
        {turn.usage ? (
          <div className="mono text-sm text-[var(--color-ink-soft)]">
            {`${turn.usage.input_tokens.toLocaleString()} in / ${turn.usage.output_tokens.toLocaleString()} out`}
          </div>
        ) : null}
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <section className="pixel-card-soft p-4">
          <div className="pixel-label mb-3 flex items-center gap-2">
            <MessageSquareText size={14} />
            user prompt
          </div>
          <div className="whitespace-pre-wrap text-[0.98rem] leading-7 text-[var(--color-ink)]">
            {turn.prompt?.trim() || "No direct user prompt was captured for this assistant turn."}
          </div>
        </section>

        <section className="pixel-card-soft p-4">
          <div className="pixel-label mb-3 flex items-center gap-2">
            <Sparkles size={14} />
            assistant response
          </div>
          <div className="whitespace-pre-wrap text-[0.98rem] leading-7 text-[var(--color-ink-soft)]">
            {turn.answer?.trim() || (turn.streaming ? "Streaming response..." : "No text response.")}
          </div>
        </section>
      </div>

      {turn.runMeta ? (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="pixel-tag">{turn.runMeta.status}</span>
          <span className="pixel-tag">{turn.runMeta.orchestration_engine || "langgraph"}</span>
          {turn.runMeta.thread_id ? (
            <span className="mono text-[0.92rem] text-[var(--color-ink-soft)]">
              thread {turn.runMeta.thread_id}
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="mt-4">
        {turn.hitlEvents.length ? (
          <div className="pixel-card-soft mb-4 px-4 py-3 text-sm text-[var(--color-ink-soft)]">
            <p className="pixel-label mb-3">hitl trace</p>
            <div className="space-y-2">
              {turn.hitlEvents.map((item, index) => (
                <div key={`${item.type}-${item.checkpoint_id}-${index}`} className="flex flex-wrap items-center gap-2">
                  <span className="pixel-tag">{item.type}</span>
                  <span>{item.display_name}</span>
                  <span className="pixel-tag">{item.risk_level}</span>
                  {item.request_id ? (
                    <span className="mono text-[0.92rem] text-[var(--color-ink-soft)]">
                      request {item.request_id}
                    </span>
                  ) : null}
                  {item.decision_id ? (
                    <span className="mono text-[0.92rem] text-[var(--color-ink-soft)]">
                      decision {item.decision_id}
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {turn.checkpointEvents.length ? (
          <div className="pixel-card-soft mb-4 px-4 py-3 text-sm text-[var(--color-ink-soft)]">
            <p className="pixel-label mb-3">checkpoint trace</p>
            <div className="space-y-2">
              {turn.checkpointEvents.map((item, index) => (
                <div key={`${item.type}-${item.checkpoint_id}-${index}`} className="flex flex-wrap items-center gap-2">
                  <span className="pixel-tag">{item.type}</span>
                  <span className="mono text-[0.92rem] text-[var(--color-ink-soft)]">
                    {item.checkpoint_id || "pending checkpoint"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {turn.retrievalSteps.length ? <RetrievalCard steps={turn.retrievalSteps} /> : null}
        {turn.toolCalls.length ? <ThoughtChain toolCalls={turn.toolCalls} /> : null}
        {!hasTrace ? (
          <div className="pixel-card-soft px-4 py-3 text-sm text-[var(--color-ink-soft)]">
            No retrieval or tool trace was emitted for this turn.
          </div>
        ) : null}
      </div>
    </article>
  );
});

export function TracePanel() {
  const { messages, streamingMessages, isStreaming } = useChatStore();
  const [view, setView] = useState<"execution" | "context">("context");
  const turnCacheRef = useRef(
    new Map<
      string,
      {
        source: ReturnType<typeof useChatStore>["messages"][number];
        prompt: string | null;
        streaming: boolean;
        turn: TraceTurn;
      }
    >()
  );

  const turns = useMemo(() => {
    const combined = [...messages, ...streamingMessages];
    const streamingIds = new Set(streamingMessages.map((item) => item.id));
    const nextCache = new Map<
      string,
      {
        source: ReturnType<typeof useChatStore>["messages"][number];
        prompt: string | null;
        streaming: boolean;
        turn: TraceTurn;
      }
    >();
    const nextTurns: TraceTurn[] = [];
    let lastUserPrompt: string | null = null;

    for (const message of combined) {
      if (message.role === "user") {
        lastUserPrompt = message.content;
        continue;
      }

      const streaming = isStreaming && streamingIds.has(message.id);
      const cached = turnCacheRef.current.get(message.id);

      if (cached && cached.source === message && cached.prompt === lastUserPrompt && cached.streaming === streaming) {
        nextCache.set(message.id, cached);
        nextTurns.push(cached.turn);
        continue;
      }

      const turn: TraceTurn = {
        id: message.id,
        prompt: lastUserPrompt,
        answer: message.content,
        toolCalls: message.toolCalls,
        retrievalSteps: message.retrievalSteps,
        usage: message.usage,
        runMeta: message.runMeta,
        checkpointEvents: message.checkpointEvents,
        hitlEvents: message.hitlEvents,
        streaming
      };

      nextCache.set(message.id, { source: message, prompt: lastUserPrompt, streaming, turn });
      nextTurns.push(turn);
    }

    turnCacheRef.current = nextCache;
    return nextTurns.reverse();
  }, [isStreaming, messages, streamingMessages]);

  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <div className="panel flex min-h-0 flex-1 flex-col px-4 pb-4 pt-4">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <button
            className={`pixel-button px-4 py-2 text-sm ${view === "context" ? "is-active" : ""}`}
            onClick={() => setView("context")}
            type="button"
          >
            Model-visible context
          </button>
          <button
            className={`pixel-button px-4 py-2 text-sm ${view === "execution" ? "is-active" : ""}`}
            onClick={() => setView("execution")}
            type="button"
          >
            Execution trace
          </button>
        </div>

        {view === "context" ? (
          <div className="trace-scroll-area flex-1 overflow-y-auto pr-2">
            <ContextTracePanel />
          </div>
        ) : !turns.length ? (
          <div className="trace-scroll-area flex-1 overflow-y-auto pr-2">
            <div className="pixel-card-soft px-6 py-8">
              <p className="pixel-label">ready</p>
              <h3 className="pixel-title mt-3 text-[1rem] text-[var(--color-ink)]">
                Trace opens only when you need it
              </h3>
              <p className="pixel-note mt-4 max-w-3xl">
                Retrieval, tools, checkpoint events, and HITL stay fully available here without weighing down the default chat surface.
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
              <div className="pb-4">
                <TraceTurnCard turn={turn} />
              </div>
            )}
          />
        )}
      </div>
    </section>
  );
}
