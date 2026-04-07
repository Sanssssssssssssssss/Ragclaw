"use client";

import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { MessageUsage, RetrievalStep, RunMeta, ToolCall } from "@/lib/api";

/**
 * Returns one human-readable usage label from one usage object input and formats the token summary for a turn.
 */
function formatTokenUsage(usage: MessageUsage) {
  return `Input ${usage.input_tokens.toLocaleString()} | Output ${usage.output_tokens.toLocaleString()} tokens`;
}

/**
 * Returns one rendered chat message from role, content, and usage inputs and keeps the main chat lightweight.
 */
export const ChatMessage = memo(function ChatMessage({
  role,
  content,
  usage,
  runMeta,
  streaming = false
}: {
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCall[];
  retrievalSteps?: RetrievalStep[];
  usage: MessageUsage | null;
  runMeta?: RunMeta | null;
  streaming?: boolean;
}) {
  const isUser = role === "user";
  const articleRef = useRef<HTMLElement | null>(null);
  const [canRenderMarkdown, setCanRenderMarkdown] = useState(isUser || streaming);

  useEffect(() => {
    if (isUser || streaming || canRenderMarkdown) {
      return;
    }

    const node = articleRef.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      setCanRenderMarkdown(true);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setCanRenderMarkdown(true);
          observer.disconnect();
        }
      },
      {
        root: null,
        rootMargin: "320px 0px"
      }
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, [canRenderMarkdown, isUser, streaming]);

  const shouldRenderPlainText = isUser || streaming || !canRenderMarkdown;

  return (
    <article
      className={`message-card max-w-[92%] rounded-[28px] border px-5 py-4 ${
        isUser
          ? "ml-auto border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.07)] text-white"
          : "mr-auto border-[var(--color-line)] bg-[rgba(255,255,255,0.03)] text-[var(--color-ink)]"
      }`}
      ref={articleRef}
    >
      {!isUser && runMeta ? (
        <div className="mb-3 flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-[var(--color-ink-muted)]">
          <span className="rounded-full border border-[var(--color-line)] px-2 py-1">
            {runMeta.status}
          </span>
          {runMeta.checkpoint_id ? (
            <span className="mono text-[11px] normal-case tracking-normal text-[var(--color-ink-soft)]">
              checkpoint {runMeta.checkpoint_id.slice(0, 8)}
            </span>
          ) : null}
        </div>
      ) : null}
      <div
        className={
          shouldRenderPlainText
            ? "whitespace-pre-wrap text-[1rem] leading-8 text-[var(--color-ink)]"
            : "markdown"
        }
      >
        {shouldRenderPlainText ? (
          content || (streaming ? "Thinking..." : "")
        ) : (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || "Thinking..."}</ReactMarkdown>
        )}
      </div>
      {!isUser && usage && (
        <div className="mt-4 border-t border-[var(--color-line)] pt-3 text-sm text-[var(--color-ink-soft)]">
          {formatTokenUsage(usage)}
        </div>
      )}
    </article>
  );
});
