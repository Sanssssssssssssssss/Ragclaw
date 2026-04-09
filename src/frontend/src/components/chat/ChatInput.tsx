"use client";

import { SendHorizonal } from "lucide-react";
import { useState } from "react";

export function ChatInput({
  disabled,
  onSend
}: {
  disabled: boolean;
  onSend: (value: string) => Promise<void>;
}) {
  const [value, setValue] = useState("");

  const submitValue = () => {
    const nextValue = value.trim();
    if (!nextValue || disabled) {
      return;
    }
    void onSend(nextValue);
    setValue("");
  };

  return (
    <form
      className="panel shrink-0 px-4 py-4"
      onSubmit={(event) => {
        event.preventDefault();
        submitValue();
      }}
    >
      <textarea
        className="pixel-field min-h-28 resize-none px-5 py-5 text-[1rem] leading-7 placeholder:text-[var(--color-ink-muted)]"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          const isEnter =
            event.key === "Enter" || event.code === "Enter" || event.code === "NumpadEnter";
          if ((event.metaKey || event.ctrlKey) && isEnter && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submitValue();
          }
        }}
        placeholder="随便问点什么… “优化数据库查询”"
        value={value}
      />
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <p className="mono text-[0.92rem] text-[var(--color-ink-soft)]">Ctrl/Cmd + Enter to send.</p>
        <button className="ui-button ui-button-primary" disabled={disabled || !value.trim()} type="submit">
          <SendHorizonal size={16} />
          Send
        </button>
      </div>
    </form>
  );
}
