"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { RetrievalCard } from "@/components/chat/RetrievalCard";
import { ThoughtChain } from "@/components/chat/ThoughtChain";
import type { MessageUsage, RetrievalStep, ToolCall } from "@/lib/api";

/**
 * Returns one human-readable usage label from one usage object input and formats the token summary for a turn.
 */
function formatTokenUsage(usage: MessageUsage) {
  return `Input ${usage.input_tokens.toLocaleString()} | Output ${usage.output_tokens.toLocaleString()} tokens`;
}

/**
 * Returns one rendered chat message from role, content, tool, retrieval, and usage inputs and draws one message bubble.
 */
export const ChatMessage = memo(function ChatMessage({
  role,
  content,
  toolCalls,
  retrievalSteps,
  usage,
  streaming = false
}: {
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  retrievalSteps: RetrievalStep[];
  usage: MessageUsage | null;
  streaming?: boolean;
}) {
  const isUser = role === "user";
  const shouldRenderPlainText = isUser || streaming;

  return (
    <article
      className={`message-card max-w-[92%] rounded-[28px] border px-5 py-4 ${
        isUser
          ? "ml-auto border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.07)] text-white"
          : "mr-auto border-[var(--color-line)] bg-[rgba(255,255,255,0.03)] text-[var(--color-ink)]"
      }`}
    >
      {!isUser && <RetrievalCard steps={retrievalSteps} />}
      {!isUser && <ThoughtChain toolCalls={toolCalls} />}
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
