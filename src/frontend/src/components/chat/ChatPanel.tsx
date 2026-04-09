"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { ChatMessage } from "@/components/chat/ChatMessage";
import { VirtualizedStack } from "@/components/chat/VirtualizedStack";
import { PixelGhostFriend } from "@/components/icons/PixelGhostFriend";
import { useChatStore, useSessionStore } from "@/lib/store";

const AUTO_SCROLL_THRESHOLD = 72;
const CHAT_ITEM_ESTIMATE = 220;

type ChatRow = {
  id: string;
  message: ReturnType<typeof useChatStore>["messages"][number];
  streaming: boolean;
};

export function ChatPanel() {
  const {
    messages,
    pendingHitl,
    streamingMessages,
    submitHitlDecision,
    isInitializing,
    isSessionLoading,
    isStreaming,
    connectionError,
    retryInitialization
  } = useChatStore();
  const [editedInputText, setEditedInputText] = useState("{}");
  const [editError, setEditError] = useState("");
  const { currentSessionId, sessions } = useSessionStore();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const frameRef = useRef<number | null>(null);
  const lastScrollTopRef = useRef(0);
  const programmaticScrollRef = useRef(false);
  const rowCacheRef = useRef(
    new Map<string, { source: ChatRow["message"]; streaming: boolean; row: ChatRow }>()
  );

  const renderableMessages = useMemo(() => {
    const nextCache = new Map<
      string,
      { source: ChatRow["message"]; streaming: boolean; row: ChatRow }
    >();
    const nextRows: ChatRow[] = [];

    const pushRow = (message: ChatRow["message"], streaming: boolean) => {
      const cached = rowCacheRef.current.get(message.id);
      if (cached && cached.source === message && cached.streaming === streaming) {
        nextCache.set(message.id, cached);
        nextRows.push(cached.row);
        return;
      }

      const row = { id: message.id, message, streaming };
      const entry = { source: message, streaming, row };
      nextCache.set(message.id, entry);
      nextRows.push(row);
    };

    for (const message of messages) {
      pushRow(message, false);
    }

    for (let index = 0; index < streamingMessages.length; index += 1) {
      pushRow(streamingMessages[index], isStreaming && index === streamingMessages.length - 1);
    }

    rowCacheRef.current = nextCache;
    return nextRows;
  }, [isStreaming, messages, streamingMessages]);

  const lastMessage = renderableMessages[renderableMessages.length - 1];
  const recentSessions = useMemo(
    () => sessions.filter((session) => session.id !== currentSessionId).slice(0, 2),
    [currentSessionId, sessions]
  );

  useEffect(() => {
    setEditedInputText(JSON.stringify(pendingHitl?.proposed_input ?? {}, null, 2));
    setEditError("");
  }, [pendingHitl]);

  const cancelScheduledScroll = useCallback(() => {
    if (frameRef.current !== null) {
      window.cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
  }, []);

  const syncToBottom = useCallback((defer = true) => {
    const container = scrollRef.current;
    if (!container || !stickToBottomRef.current) {
      return;
    }

    const run = () => {
      const nextContainer = scrollRef.current;
      if (!nextContainer || !stickToBottomRef.current) {
        return;
      }
      programmaticScrollRef.current = true;
      nextContainer.scrollTop = nextContainer.scrollHeight;
      lastScrollTopRef.current = nextContainer.scrollTop;
    };

    cancelScheduledScroll();

    if (!defer) {
      run();
      return;
    }

    frameRef.current = window.requestAnimationFrame(() => {
      frameRef.current = null;
      run();
    });
  }, [cancelScheduledScroll]);

  useLayoutEffect(() => {
    const container = scrollRef.current;
    if (!container) {
      return;
    }

    const handleScroll = () => {
      const nextScrollTop = container.scrollTop;
      const distanceToBottom =
        container.scrollHeight - nextScrollTop - container.clientHeight;

      if (programmaticScrollRef.current) {
        programmaticScrollRef.current = false;
        lastScrollTopRef.current = nextScrollTop;
        stickToBottomRef.current = distanceToBottom <= AUTO_SCROLL_THRESHOLD;
        return;
      }

      const scrolledUp = nextScrollTop < lastScrollTopRef.current;
      lastScrollTopRef.current = nextScrollTop;

      if (scrolledUp) {
        stickToBottomRef.current = false;
        cancelScheduledScroll();
      } else if (distanceToBottom <= AUTO_SCROLL_THRESHOLD) {
        stickToBottomRef.current = true;
      }

      if (!stickToBottomRef.current) {
        cancelScheduledScroll();
      }
    };

    const handleWheel = (event: WheelEvent) => {
      if (event.deltaY < 0) {
        stickToBottomRef.current = false;
        cancelScheduledScroll();
      }
    };

    const handlePointerDown = () => {
      cancelScheduledScroll();
    };

    handleScroll();
    container.addEventListener("scroll", handleScroll, { passive: true });
    container.addEventListener("wheel", handleWheel, { passive: true });
    container.addEventListener("pointerdown", handlePointerDown, { passive: true });
    return () => {
      container.removeEventListener("scroll", handleScroll);
      container.removeEventListener("wheel", handleWheel);
      container.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [cancelScheduledScroll, syncToBottom]);

  useLayoutEffect(() => {
    if (!renderableMessages.length) {
      return;
    }
    syncToBottom(false);
  }, [renderableMessages.length, syncToBottom]);

  useLayoutEffect(() => {
    stickToBottomRef.current = true;
    syncToBottom(false);
  }, [currentSessionId, syncToBottom]);

  useLayoutEffect(() => {
    if (!lastMessage) {
      return;
    }
    syncToBottom(true);
  }, [isStreaming, lastMessage, syncToBottom]);

  const handleTotalHeightChange = useCallback(() => {
    if (stickToBottomRef.current) {
      syncToBottom(false);
    }
  }, [syncToBottom]);

  const handleEditAndContinue = useCallback(() => {
    if (!pendingHitl) return;
    try {
      const parsed = JSON.parse(editedInputText) as Record<string, unknown>;
      setEditError("");
      void submitHitlDecision(pendingHitl.checkpoint_id, "edit", parsed);
    } catch (error) {
      setEditError(error instanceof Error ? error.message : "Invalid JSON");
    }
  }, [editedInputText, pendingHitl, submitHitlDecision]);

  return (
    <section className="flex h-full min-h-0 flex-1 flex-col overflow-hidden">
      <div className="panel flex min-h-0 flex-1 flex-col overflow-hidden px-4 pb-4 pt-4">
        {connectionError ? (
          <div className="pixel-card-soft mb-4 px-4 py-4 text-sm text-[var(--color-ink)]">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="pixel-label">Backend unavailable</p>
                <p className="pixel-note mt-2">{connectionError}</p>
              </div>
              <button
                className="ui-button"
                disabled={isInitializing || isStreaming}
                onClick={() => void retryInitialization()}
                type="button"
              >
                {isInitializing ? "Retrying..." : "Retry"}
              </button>
            </div>
          </div>
        ) : null}

        {pendingHitl && currentSessionId ? (
          <div className="pixel-card-soft mb-4 px-4 py-4">
            <p className="pixel-label">Approval required</p>
            <h3 className="pixel-title mt-2 text-[1rem] text-[var(--color-ink)]">
              {pendingHitl.display_name}
            </h3>
            <p className="pixel-note mt-2">{pendingHitl.reason}</p>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <span className="pixel-tag">risk {pendingHitl.risk_level}</span>
              {pendingHitl.request_id ? <span className="pixel-tag">request {pendingHitl.request_id.slice(0, 8)}</span> : null}
              <span className="mono text-[0.92rem] text-[var(--color-ink-soft)]">
                {JSON.stringify(pendingHitl.proposed_input)}
              </span>
            </div>
            <label className="pixel-label mt-4 block">Edit payload</label>
            <textarea
              className="mt-2 min-h-[120px] w-full rounded-[8px] border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-3 font-mono text-sm text-[var(--color-ink)] outline-none"
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
                {isStreaming ? "Approving..." : "Approve"}
              </button>
              <button
                className="ui-button"
                disabled={isStreaming}
                onClick={handleEditAndContinue}
                type="button"
              >
                {isStreaming ? "Editing..." : "Edit and continue"}
              </button>
              <button
                className="ui-button"
                disabled={isStreaming}
                onClick={() => void submitHitlDecision(pendingHitl.checkpoint_id, "reject")}
                type="button"
              >
                {isStreaming ? "Rejecting..." : "Reject"}
              </button>
            </div>
          </div>
        ) : null}

        {!renderableMessages.length ? (
          <div className="chat-scroll-area flex-1 overflow-y-auto pr-2" ref={scrollRef}>
            <div className="cli-welcome px-6 py-7">
              <div className="cli-welcome-bar">ragclaw local // kimi-k2.5</div>
              <div className="cli-welcome-grid mt-8">
                <section className="cli-welcome-main">
                  <h3 className="pixel-title text-[2.3rem] text-[var(--color-ink)]">
                    {isInitializing
                      ? "Starting workspace"
                      : isSessionLoading
                        ? "Switching thread"
                        : "Build anything"}
                  </h3>
                  <div className="cli-emoji-wrap mt-8">
                    <div className="cli-emoji">
                      <PixelGhostFriend className="h-full w-full" />
                    </div>
                  </div>
                  <div className="mt-8 text-center">
                    <p className="mono text-[1rem] text-[var(--color-ink-soft)]">
                      kimi-k2.5 / api usage billing
                    </p>
                    <p className="mono mt-2 text-[1rem] text-[var(--color-ink)]">
                      $0.60 input / $3.00 output / $0.10 cache hit
                    </p>
                    <p className="mt-4 text-sm text-[var(--color-ink-muted)]">
                      <a className="underline underline-offset-4" href="https://pxlkit.xyz" rel="noreferrer" target="_blank">
                        Icons by Pxlkit
                      </a>
                    </p>
                  </div>
                </section>

                <section className="cli-welcome-side">
                  <div>
                    <h4 className="pixel-title text-[1rem] text-[var(--color-ink)]">Tips</h4>
                    <div className="mono mt-4 space-y-3 text-[1rem] leading-8 text-[var(--color-ink-soft)]">
                      <p>Use Trace when you want retrieval, tools, checkpoints, and HITL separated from the main run.</p>
                      <p>Open Tools for files, runtime toggles, and session controls.</p>
                      <p>Use Ctrl/Cmd + Enter to send. Only the chat viewport scrolls.</p>
                    </div>
                  </div>

                  <div className="cli-divider" />

                  <div>
                    <h4 className="pixel-title text-[1rem] text-[var(--color-ink)]">Recent activity</h4>
                    <div className="mono mt-4 space-y-2 text-[1rem] text-[var(--color-ink-soft)]">
                      {recentSessions.length ? (
                        recentSessions.map((session) => (
                          <div className="flex flex-wrap items-center justify-between gap-3" key={session.id}>
                            <span className="truncate">{session.title}</span>
                            <span className="text-[var(--color-ink-muted)]">{session.message_count} msgs</span>
                          </div>
                        ))
                      ) : (
                        <p>No earlier sessions yet.</p>
                      )}
                    </div>
                  </div>
                </section>
              </div>
            </div>
          </div>
        ) : (
          <VirtualizedStack
            className="chat-scroll-area flex-1 overflow-y-auto pr-2"
            containerRef={scrollRef}
            estimateHeight={CHAT_ITEM_ESTIMATE}
            getKey={(row) => row.id}
            items={renderableMessages}
            onTotalHeightChange={handleTotalHeightChange}
            renderItem={(row) => (
              <div className="pb-4">
                <ChatMessage
                  content={row.message.content}
                  role={row.message.role}
                  runMeta={row.message.runMeta}
                  streaming={row.streaming}
                  usage={row.message.usage}
                />
              </div>
            )}
          />
        )}
      </div>
    </section>
  );
}
