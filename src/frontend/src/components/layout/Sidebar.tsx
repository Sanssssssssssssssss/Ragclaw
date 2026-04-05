"use client";

import { memo } from "react";
import { MessageSquare, Plus, Trash2 } from "lucide-react";

import { useFeedStore, useSessionStore } from "@/lib/store";

/**
 * Returns one preview string from a raw text input and truncates long message bodies for the sidebar list.
 */
function preview(text: string) {
  return text.length > 90 ? `${text.slice(0, 90)}...` : text;
}

const RawMessagePreview = memo(function RawMessagePreview({
  role,
  toolCount,
  content
}: {
  role: "user" | "assistant";
  toolCount: number;
  content: string;
}) {
  return (
    <div className="rounded-2xl border border-[var(--color-line)] bg-[rgba(255,255,255,0.03)] px-3 py-3">
      <div className="mb-1 flex items-center justify-between text-[11px] uppercase tracking-[0.18em] text-[var(--color-ink-muted)]">
        <span>{role}</span>
        <span>{toolCount} tools</span>
      </div>
      <p className="text-sm leading-6 text-[var(--color-ink-soft)]">{preview(content)}</p>
    </div>
  );
});

/**
 * Returns one rendered sidebar from no explicit inputs and shows sessions plus raw message previews.
 */
export function Sidebar() {
  const { sessions, currentSessionId, selectSession, createNewSession, removeSession } =
    useSessionStore();
  const { messageFeed } = useFeedStore();
  const previewMessages = [...messageFeed].reverse();

  return (
    <aside className="panel flex h-full min-h-0 flex-col rounded-[28px] p-4">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.3em] text-[var(--color-ink-muted)]">
            Sessions
          </p>
          <h2 className="text-xl font-semibold tracking-[-0.04em] text-white">Threads</h2>
        </div>
        <button
          className="ui-button h-10 w-10 rounded-2xl p-0"
          onClick={() => void createNewSession()}
          type="button"
        >
          <Plus size={18} />
        </button>
      </div>

      <div className="min-h-0 space-y-2 overflow-y-auto pr-1">
        {sessions.map((session) => (
          <div
            className={`rounded-[24px] border px-4 py-3 transition ${
              session.id === currentSessionId
                ? "border-[rgba(16,163,127,0.24)] bg-[rgba(16,163,127,0.12)]"
                : "border-[var(--color-line)] bg-[rgba(255,255,255,0.02)]"
            }`}
            key={session.id}
          >
            <button
              className="w-full text-left"
              onClick={() => void selectSession(session.id)}
              type="button"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-base font-medium text-white">{session.title}</p>
                  <p className="mt-1 text-sm text-[var(--color-ink-soft)]">
                    {session.message_count} messages
                  </p>
                </div>
                <MessageSquare className="mt-1 shrink-0 text-[var(--color-ink-muted)]" size={16} />
              </div>
            </button>
            <button
              className="ui-button ui-button-danger mt-3 w-full justify-center rounded-2xl"
              onClick={() => void removeSession(session.id)}
              type="button"
            >
              <Trash2 size={14} />
              Delete
            </button>
          </div>
        ))}
      </div>

      <div className="mt-4 flex min-h-0 flex-1 flex-col rounded-[24px] border border-[var(--color-line)] bg-[rgba(255,255,255,0.02)] p-3">
        <p className="text-xs uppercase tracking-[0.3em] text-[var(--color-ink-muted)]">
          Live feed
        </p>
        <div className="mt-3 min-h-0 space-y-3 overflow-y-auto pr-1">
          {previewMessages.map((message) => (
            <RawMessagePreview
              content={message.content}
              key={message.id}
              role={message.role}
              toolCount={message.toolCalls.length}
            />
          ))}
        </div>
      </div>
    </aside>
  );
}
