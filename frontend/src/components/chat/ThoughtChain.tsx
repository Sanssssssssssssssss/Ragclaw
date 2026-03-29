"use client";

import { TerminalSquare } from "lucide-react";
import { memo, useEffect, useMemo, useState } from "react";

import type { ToolCall } from "@/lib/api";

/**
 * Returns one formatted text block from a raw string input and prettifies tool inputs or outputs for display.
 */
function formatBlock(value: string) {
  const text = value.trim();
  if (!text) {
    return "Empty";
  }

  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

/**
 * Returns one rendered tool-call panel from tool-call inputs and visualizes the current tool execution trace.
 */
export const ThoughtChain = memo(function ThoughtChain({ toolCalls }: { toolCalls: ToolCall[] }) {
  const activeTool = [...toolCalls].reverse().find((toolCall) => !toolCall.output.trim()) ?? null;
  const toolNames = useMemo(
    () => Array.from(new Set(toolCalls.map((toolCall) => toolCall.tool))),
    [toolCalls]
  );
  const [isOpen, setIsOpen] = useState(Boolean(activeTool));

  useEffect(() => {
    if (activeTool) {
      setIsOpen(true);
    }
  }, [activeTool, toolCalls.length]);

  if (!toolCalls.length) {
    return null;
  }

  return (
    <details
      className="mb-4 rounded-3xl border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.03)] p-4"
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
      open={isOpen}
    >
      <summary className="flex cursor-pointer list-none items-start gap-3 text-sm font-medium uppercase tracking-[0.18em] text-[var(--color-ink-soft)]">
        <TerminalSquare className="mt-0.5 shrink-0 text-[var(--color-accent)]" size={16} />
        <div className="min-w-0 flex-1">
          <div className="text-white">
            {activeTool ? `Running ${activeTool.tool}` : `${toolCalls.length} tool call(s)`}
          </div>
          <div className="truncate pt-1 text-xs font-normal tracking-[0.16em] text-[var(--color-ink-muted)]">
            {toolNames.join(" -> ")}
          </div>
        </div>
        <span className="shrink-0 text-[11px] font-normal tracking-[0.16em] text-[var(--color-ink-muted)]">
          {isOpen ? "Collapse" : "Expand"}
        </span>
      </summary>

      <div className="mt-3 space-y-3">
        {toolCalls.map((toolCall, index) => {
          const isFinished = Boolean(toolCall.output.trim());

          return (
            <div
              className="rounded-2xl border border-[rgba(255,255,255,0.06)] bg-[rgba(0,0,0,0.2)] p-3"
              key={`${toolCall.tool}-${index}`}
            >
              <div className="mb-2 flex items-center justify-between gap-3 text-sm font-medium">
                <span className="text-white">{toolCall.tool}</span>
                <span
                  className={`rounded-full px-2 py-1 text-[11px] uppercase tracking-[0.16em] ${
                    isFinished
                      ? "bg-[rgba(16,163,127,0.14)] text-[#7fe7ca]"
                      : "bg-[rgba(255,255,255,0.08)] text-[var(--color-ink-soft)]"
                  }`}
                >
                  {isFinished ? "Completed" : "Running"}
                </span>
              </div>

              <div className="space-y-2 text-sm">
                <div className="rounded-2xl border border-[rgba(255,255,255,0.06)] bg-[rgba(255,255,255,0.03)] p-3">
                  <div className="mb-1 font-medium uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
                    Input
                  </div>
                  <pre className="mono whitespace-pre-wrap text-[var(--color-ink-soft)]">
                    {formatBlock(toolCall.input)}
                  </pre>
                </div>
                <div className="rounded-2xl border border-[rgba(255,255,255,0.06)] bg-[rgba(255,255,255,0.03)] p-3">
                  <div className="mb-1 font-medium uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
                    Output
                  </div>
                  <pre className="mono whitespace-pre-wrap text-[var(--color-ink-soft)]">
                    {formatBlock(toolCall.output)}
                  </pre>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </details>
  );
});
