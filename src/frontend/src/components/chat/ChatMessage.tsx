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

const DOODLE_FRAMES = ["[+__+]", "[+o_+]", "[+O_+]", "[+o_+]", "[+__+]", "[+^^+]"];

function StreamingThinking() {
  const [frameIndex, setFrameIndex] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setFrameIndex((value) => (value + 1) % DOODLE_FRAMES.length);
    }, 180);

    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="doodle-thinking" aria-label="thinking">
      <span className="doodle-frame mono">{DOODLE_FRAMES[frameIndex]}</span>
      <span className="mono">thinking...</span>
    </div>
  );
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
      className={`message-card max-w-[92%] px-4 py-3 ${
        isUser
          ? "pixel-card-soft ml-auto text-[var(--color-ink)]"
          : "pixel-card mr-auto text-[var(--color-ink)]"
      }`}
      ref={articleRef}
    >
      {!isUser && runMeta ? (
        <div className="mb-3 flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-[var(--color-ink-muted)]">
          <span className="pixel-tag">
            {runMeta.status}
          </span>
          {runMeta.checkpoint_id ? (
            <span className="mono rounded-[4px] border border-[var(--color-line)] bg-[var(--color-bg)] px-2 py-1 text-[0.84rem] normal-case tracking-normal text-[var(--color-ink-soft)]">
              checkpoint {runMeta.checkpoint_id.slice(0, 8)}
            </span>
          ) : null}
        </div>
      ) : null}
      <div
        className={
          shouldRenderPlainText
            ? "whitespace-pre-wrap text-[0.98rem] leading-7 text-[var(--color-ink)]"
            : "markdown"
        }
      >
        {shouldRenderPlainText ? (
          content ||
          (runMeta?.status === "interrupted"
            ? "Approval requested before capability execution."
            : streaming
              ? <StreamingThinking />
              : "")
        ) : (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {content || (runMeta?.status === "interrupted" ? "Approval requested before capability execution." : "Thinking...")}
          </ReactMarkdown>
        )}
      </div>
      {!isUser && usage && (
        <div className="mono mt-4 border-t border-solid border-[var(--color-line)] pt-3 text-[0.9rem] text-[var(--color-ink-soft)]">
          {formatTokenUsage(usage)}
        </div>
      )}
    </article>
  );
});
