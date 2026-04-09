"use client";

import { Search, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";

import { VirtualizedStack } from "@/components/chat/VirtualizedStack";
import { useSessionStore } from "@/lib/store";

const SESSION_ROW_ESTIMATE = 108;

export function Sidebar({ onClose }: { onClose: () => void }) {
  const { sessions, currentSessionId, selectSession, removeSession } = useSessionStore();
  const [query, setQuery] = useState("");

  const filteredSessions = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) {
      return sessions;
    }

    return sessions.filter((session) => session.title.toLowerCase().includes(normalized));
  }, [query, sessions]);

  return (
    <div className="drawer-root">
      <button aria-label="Close sessions drawer" className="drawer-backdrop" onClick={onClose} type="button" />
      <aside className="drawer-panel drawer-panel-left">
        <div className="flex items-start justify-between gap-3 border-b border-[var(--color-line)] px-5 py-5">
          <div>
            <p className="pixel-label">
              # Sessions
            </p>
            <h2 className="pixel-title mt-2 text-[0.92rem] text-[var(--color-ink)]">Thread rail</h2>
          </div>
          <button className="ui-button" onClick={onClose} type="button">
            <X size={16} />
            Close
          </button>
        </div>

        <div className="border-b border-[var(--color-line)] px-5 py-4">
          <label className="relative block">
            <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-ink-muted)]" size={16} />
            <input
              className="pixel-field py-2 pl-10 pr-4 text-sm"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="# SEARCH SESSIONS"
              value={query}
            />
          </label>
        </div>

        <div className="min-h-0 flex-1 px-3 py-3">
          <VirtualizedStack
            className="h-full overflow-y-auto pr-2"
            estimateHeight={SESSION_ROW_ESTIMATE}
            getKey={(session) => session.id}
            items={filteredSessions}
            renderItem={(session) => (
              <div className="pb-3">
                <article
                  className={`px-4 py-3 ${
                    session.id === currentSessionId
                      ? "pixel-card-soft"
                      : "pixel-card"
                  }`}
                >
                  <button
                    className="w-full text-left"
                    onClick={() => {
                      void selectSession(session.id);
                      onClose();
                    }}
                    type="button"
                  >
                    <p className="pixel-title truncate text-[0.78rem] text-[var(--color-ink)]">
                      {session.title}
                    </p>
                    <p className="mono mt-3 text-[1rem] text-[var(--color-ink-muted)]">
                      {session.message_count} messages
                    </p>
                  </button>

                  <button
                    className="ui-button ui-button-danger mt-3 w-full justify-center"
                    onClick={() => void removeSession(session.id)}
                    type="button"
                  >
                    <Trash2 size={14} />
                    Delete
                  </button>
                </article>
              </div>
            )}
          />
        </div>
      </aside>
    </div>
  );
}
