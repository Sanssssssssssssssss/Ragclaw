"use client";

import { SendHorizonal } from "lucide-react";
import { useState } from "react";

/**
 * Returns one rendered chat input from disabled state and send handler inputs and captures a user prompt for submission.
 */
export function ChatInput({
  disabled,
  onSend
}: {
  disabled: boolean;
  onSend: (value: string) => Promise<void>;
}) {
  const [value, setValue] = useState("");

  return (
    <div className="panel rounded-[28px] px-4 py-3">
      <textarea
        className="min-h-36 w-full resize-none rounded-[24px] border border-[var(--color-line)] bg-[rgba(255,255,255,0.03)] px-5 py-4 text-base leading-8 text-white outline-none transition placeholder:text-[var(--color-ink-muted)] focus:border-[var(--color-accent-strong)] focus:bg-[rgba(255,255,255,0.05)]"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault();
            const nextValue = value.trim();
            if (!nextValue) {
              return;
            }
            void onSend(nextValue);
            setValue("");
          }
        }}
        placeholder="Message Onyx Chat. Press Ctrl/Cmd + Enter to send."
        value={value}
      />
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-[var(--color-ink-soft)]">
          Same actions and APIs, refreshed into a calmer dark workspace.
        </p>
        <button
          className="ui-button ui-button-primary"
          disabled={disabled || !value.trim()}
          onClick={() => {
            const nextValue = value.trim();
            if (!nextValue) {
              return;
            }
            void onSend(nextValue);
            setValue("");
          }}
          type="button"
        >
          <SendHorizonal size={16} />
          Send
        </button>
      </div>
    </div>
  );
}
