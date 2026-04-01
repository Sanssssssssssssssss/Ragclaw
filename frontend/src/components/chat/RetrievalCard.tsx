"use client";

import { memo } from "react";
import { Database, FileSearch, Layers3, Search, Sparkles, type LucideIcon } from "lucide-react";

import type { RetrievalStep } from "@/lib/api";

const STEP_META: Record<
  string,
  {
    label: string;
    icon: LucideIcon;
    border: string;
    badge: string;
  }
> = {
  memory: {
    label: "Memory",
    icon: Database,
    border: "border-[rgba(16,163,127,0.22)] bg-[rgba(16,163,127,0.08)]",
    badge: "bg-[rgba(16,163,127,0.14)] text-[#7fe7ca]"
  },
  skill: {
    label: "Skill",
    icon: Search,
    border: "border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)]",
    badge: "bg-[rgba(255,255,255,0.08)] text-white"
  },
  fallback: {
    label: "Fallback",
    icon: Sparkles,
    border: "border-[rgba(255,107,107,0.2)] bg-[rgba(255,107,107,0.08)]",
    badge: "bg-[rgba(255,107,107,0.14)] text-[#ffb2b2]"
  },
  vector: {
    label: "Vector",
    icon: Database,
    border: "border-[rgba(16,163,127,0.22)] bg-[rgba(16,163,127,0.08)]",
    badge: "bg-[rgba(16,163,127,0.14)] text-[#7fe7ca]"
  },
  bm25: {
    label: "BM25",
    icon: FileSearch,
    border: "border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)]",
    badge: "bg-[rgba(255,255,255,0.08)] text-white"
  },
  fused: {
    label: "Fused",
    icon: Layers3,
    border: "border-[rgba(16,163,127,0.22)] bg-[rgba(16,163,127,0.08)]",
    badge: "bg-[rgba(16,163,127,0.14)] text-[#7fe7ca]"
  }
};

const RetrievalStepCard = memo(function RetrievalStepCard({ step }: { step: RetrievalStep }) {
  const meta = STEP_META[step.stage] ?? STEP_META.skill;
  const Icon = meta.icon;

  return (
    <section className={`rounded-2xl border p-3 ${meta.border}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={`rounded-full px-2 py-1 text-[11px] font-medium uppercase tracking-[0.18em] ${meta.badge}`}>
              {meta.label}
            </span>
            <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-white">
              <Icon className="shrink-0" size={14} />
              <span className="truncate">{step.title}</span>
            </div>
          </div>
          {step.message ? (
            <p className="mt-2 text-sm leading-7 text-[var(--color-ink-soft)]">
              {step.message}
            </p>
          ) : null}
        </div>
        {step.results.length ? (
          <span className="shrink-0 rounded-full border border-[var(--color-line)] bg-[rgba(255,255,255,0.04)] px-2 py-1 text-[11px] uppercase tracking-[0.16em] text-[var(--color-ink-soft)]">
            {step.results.length} hits
          </span>
        ) : null}
      </div>

      {!!step.results.length && (
        <div className="mt-3 space-y-2">
          {step.results.map((item, resultIndex) => (
            <div
              className="rounded-2xl border border-[rgba(255,255,255,0.06)] bg-[rgba(0,0,0,0.2)] p-3"
              key={`${item.channel}-${item.source_path}-${item.locator}-${resultIndex}`}
            >
              <div className="mb-1 flex items-center justify-between gap-3 text-xs uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
                <span className="truncate">{item.source_path}</span>
                {typeof item.score === "number" ? <span>{item.score.toFixed(3)}</span> : null}
              </div>
              {item.locator ? (
                <div className="mb-2 text-xs text-[var(--color-ink-muted)]">{item.locator}</div>
              ) : null}
              <p className="text-sm leading-7 text-[var(--color-ink)]">{item.snippet}</p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
});

/**
 * Returns one rendered retrieval card from retrieval-step inputs and shows streamed evidence for an assistant turn.
 */
export const RetrievalCard = memo(function RetrievalCard({ steps }: { steps: RetrievalStep[] }) {
  if (!steps.length) {
    return null;
  }

  return (
    <div className="mb-4 rounded-3xl border border-[rgba(16,163,127,0.2)] bg-[rgba(16,163,127,0.07)] p-4">
      <div className="flex items-center gap-2 text-sm font-medium uppercase tracking-[0.24em] text-[#88e8cf]">
        <Database size={15} />
        Retrieval trace
      </div>

      <div className="mt-3 space-y-3">
        {steps.map((step, index) => (
          <RetrievalStepCard key={`${step.kind}-${step.stage}-${index}`} step={step} />
        ))}
      </div>
    </div>
  );
});
