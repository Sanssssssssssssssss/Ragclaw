"use client";

import { useEffect, useLayoutEffect, useMemo, useRef } from "react";

import { ChatInput } from "@/components/chat/ChatInput";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { useChatStore } from "@/lib/store";

const AUTO_SCROLL_THRESHOLD = 72;

/**
 * Returns one compact scroll signature string from the latest message input and tracks layout-affecting chat changes.
 */
function buildScrollSignature(
  message:
    | {
        id: string;
        content: string;
        toolCalls: Array<{ input: string; output: string }>;
        retrievalSteps: Array<{ stage: string; title: string; message: string; results: unknown[] }>;
        usage: { input_tokens: number; output_tokens: number } | null;
      }
    | undefined
) {
  if (!message) {
    return "empty";
  }

  return [
    message.id,
    message.content.length,
    message.toolCalls.length,
    ...message.toolCalls.flatMap((toolCall) => [toolCall.input.length, toolCall.output.length]),
    message.retrievalSteps.length,
    ...message.retrievalSteps.flatMap((step) => [
      step.stage,
      step.title.length,
      step.message.length,
      step.results.length
    ]),
    message.usage?.input_tokens ?? 0,
    message.usage?.output_tokens ?? 0
  ].join(":");
}

/**
 * Returns one rendered chat panel from no explicit inputs and keeps the chat viewport pinned without scroll jitter.
 */
export function ChatPanel() {
  const {
    messages,
    sendMessage,
    isInitializing,
    isStreaming,
    connectionError,
    retryInitialization,
    tokenStats
  } = useChatStore();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const frameRef = useRef<number | null>(null);

  const lastMessage = messages[messages.length - 1];
  const scrollSignature = useMemo(
    () => buildScrollSignature(lastMessage),
    [lastMessage]
  );

  /**
   * Returns no value from an optional deferred flag input and keeps the scroll container pinned to the latest message.
   */
  const syncToBottom = (defer = true) => {
    const container = scrollRef.current;
    if (!container || !stickToBottomRef.current) {
      return;
    }

    const run = () => {
      const nextContainer = scrollRef.current;
      if (!nextContainer || !stickToBottomRef.current) {
        return;
      }
      nextContainer.scrollTop = nextContainer.scrollHeight;
    };

    if (frameRef.current !== null) {
      window.cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }

    if (!defer) {
      run();
      return;
    }

    frameRef.current = window.requestAnimationFrame(() => {
      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = null;
        run();
      });
    });
  };

  useEffect(() => {
    const container = scrollRef.current;
    if (!container) {
      return;
    }

    /**
     * Returns no value from no explicit inputs and updates whether the user is still near the bottom of the chat.
     */
    const handleScroll = () => {
      const distanceToBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight;
      stickToBottomRef.current = distanceToBottom <= AUTO_SCROLL_THRESHOLD;
    };

    handleScroll();
    container.addEventListener("scroll", handleScroll, { passive: true });
    return () => {
      container.removeEventListener("scroll", handleScroll);
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
      }
    };
  }, []);

  useLayoutEffect(() => {
    syncToBottom(false);
  }, [messages.length]);

  useLayoutEffect(() => {
    syncToBottom(true);
  }, [scrollSignature, isStreaming]);

  return (
    <section className="flex h-full min-w-0 flex-[1.5] flex-col gap-3 px-1">
      <div className="panel flex items-center justify-between rounded-[28px] px-5 py-4">
        <div>
          <p className="text-xs uppercase tracking-[0.32em] text-[var(--color-ink-muted)]">
            Live conversation
          </p>
          <h2 className="text-[1.45rem] font-semibold tracking-[-0.04em] text-white">
            Answers, retrieval, and tool traces in one stream
          </h2>
        </div>
        <div className="mono rounded-full border border-[var(--color-line)] bg-[var(--color-bg-soft)] px-3 py-1.5 text-sm text-[var(--color-ink-soft)]">
          {tokenStats ? `${tokenStats.total_tokens} tokens` : "No metrics yet"}
        </div>
      </div>

      <div className="panel flex min-h-0 flex-1 flex-col rounded-[30px] px-5 pb-4 pt-5">
        {connectionError && (
          <div className="mb-4 rounded-[24px] border border-[rgba(255,107,107,0.24)] bg-[var(--color-danger-soft)] px-4 py-3 text-[var(--color-ink)]">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs uppercase tracking-[0.24em] text-[#ff9d9d]">
                  Backend unavailable
                </p>
                <p className="mt-1 text-sm leading-7 text-[var(--color-ink-soft)]">
                  {connectionError}
                </p>
              </div>
              <button
                className="ui-button"
                disabled={isInitializing || isStreaming}
                onClick={() => void retryInitialization()}
                type="button"
              >
                {isInitializing ? "Retrying..." : "Retry connection"}
              </button>
            </div>
          </div>
        )}

        <div className="chat-scroll-area flex-1 space-y-5 overflow-y-auto pr-2" ref={scrollRef}>
          {!messages.length && (
            <div className="rounded-[30px] border border-dashed border-[var(--color-line)] bg-[var(--color-bg-soft)] px-7 py-8">
              <p className="text-xs uppercase tracking-[0.34em] text-[var(--color-ink-muted)]">
                {isInitializing ? "Starting" : connectionError ? "Waiting for backend" : "Ready"}
              </p>
              <h3 className="mt-3 max-w-3xl text-[2.7rem] font-semibold tracking-[-0.06em] text-white">
                {isInitializing
                  ? "Booting the local workspace"
                  : "A darker, cleaner command center for local agent chat"}
              </h3>
              <p className="mt-4 max-w-3xl text-lg leading-8 text-[var(--color-ink-soft)]">
                {connectionError
                  ? "The frontend is ready, but the backend has not come online yet. Once the backend is up, retry and the chat stream will resume here."
                  : "Ask questions, inspect the evidence, and keep retrieval plus tool activity visible without leaving the conversation."}
              </p>
            </div>
          )}

          {messages.map((message, index) => (
            <ChatMessage
              content={message.content}
              key={message.id}
              retrievalSteps={message.retrievalSteps}
              role={message.role}
              streaming={isStreaming && index === messages.length - 1 && message.role === "assistant"}
              toolCalls={message.toolCalls}
              usage={message.usage}
            />
          ))}
        </div>
      </div>

      <ChatInput
        disabled={isStreaming || isInitializing || Boolean(connectionError)}
        onSend={sendMessage}
      />
    </section>
  );
}
