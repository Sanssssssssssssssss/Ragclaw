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

const ToolCallRow = memo(function ToolCallRow({ toolCall }: { toolCall: ToolCall }) {
  const formattedInput = useMemo(() => formatBlock(toolCall.input), [toolCall.input]);
  const formattedOutput = useMemo(() => formatBlock(toolCall.output), [toolCall.output]);
  const isFinished = Boolean(toolCall.output.trim());

  return (
    <div className="pixel-card p-3">
      <div className="mb-2 flex items-center justify-between gap-3 text-sm font-medium">
        <span className="text-[var(--color-ink)]">{toolCall.tool}</span>
        <span
          className={`border-2 border-[rgba(0,0,0,0.3)] px-2 py-1 text-[11px] uppercase tracking-[0.16em] ${
            isFinished
              ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
              : "bg-[var(--color-surface-soft)] text-[var(--color-ink-soft)]"
          }`}
        >
          {isFinished ? "Completed" : "Running"}
        </span>
      </div>

      <div className="space-y-2 text-sm">
        <div className="pixel-card-soft p-3">
          <div className="pixel-label mb-2 text-[var(--color-accent)]">
            # Input
          </div>
          <pre className="mono whitespace-pre-wrap text-[var(--color-ink-soft)]">
            {formattedInput}
          </pre>
        </div>
        <div className="pixel-card-soft p-3">
          <div className="pixel-label mb-2 text-[var(--color-accent)]">
            # Output
          </div>
          <pre className="mono whitespace-pre-wrap text-[var(--color-ink-soft)]">
            {formattedOutput}
          </pre>
        </div>
      </div>
    </div>
  );
});

/**
 * Returns one rendered tool-call panel from tool-call inputs and visualizes the current tool execution trace.
 */
export const ThoughtChain = memo(function ThoughtChain({ toolCalls }: { toolCalls: ToolCall[] }) {
  let activeTool: ToolCall | null = null;
  for (let index = toolCalls.length - 1; index >= 0; index -= 1) {
    if (!toolCalls[index].output.trim()) {
      activeTool = toolCalls[index];
      break;
    }
  }

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
      className="pixel-card-soft mb-4 p-4"
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
      open={isOpen}
    >
      <summary className="flex cursor-pointer list-none items-start gap-3 text-sm font-medium uppercase tracking-[0.18em] text-[var(--color-ink-soft)]">
        <TerminalSquare className="mt-0.5 shrink-0 text-[var(--color-accent)]" size={16} />
        <div className="min-w-0 flex-1">
          <div className="pixel-title text-[0.76rem] text-[var(--color-ink)]">
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
        {toolCalls.map((toolCall, index) => (
          <ToolCallRow key={`${toolCall.tool}-${index}`} toolCall={toolCall} />
        ))}
      </div>
    </details>
  );
});
