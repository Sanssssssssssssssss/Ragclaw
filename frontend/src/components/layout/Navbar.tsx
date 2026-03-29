"use client";

import { Database, FileSearch, Monitor, Pencil, Plus, Sparkles, Wrench } from "lucide-react";

import { useRuntimeStore, useSessionStore } from "@/lib/store";

/**
 * Returns one rendered top navigation bar from no explicit inputs and exposes session, RAG, and index controls.
 */
export function Navbar() {
  const {
    createNewSession,
    compressCurrentSession,
    renameCurrentSession,
    sessions,
    currentSessionId
  } = useSessionStore();
  const {
    ragMode,
    toggleRagMode,
    skillRetrievalEnabled,
    toggleSkillRetrieval,
    executionPlatform,
    updateExecutionPlatform,
    rebuildKnowledgeIndex,
    knowledgeIndexStatus
  } = useRuntimeStore();

  const currentTitle =
    sessions.find((session) => session.id === currentSessionId)?.title ?? "Fresh thread";
  const isIndexBuilding = Boolean(knowledgeIndexStatus?.building);
  const knowledgeIndexLabel = isIndexBuilding ? "Rebuilding index" : "Rebuild index";
  const knowledgeIndexHint = isIndexBuilding
    ? "Index rebuild in progress"
    : knowledgeIndexStatus?.ready
      ? `${knowledgeIndexStatus.indexed_files} files indexed`
      : "Index offline";

  return (
    <header className="panel flex flex-wrap items-center justify-between gap-4 rounded-[28px] px-5 py-4">
      <div className="flex min-w-0 items-center gap-4">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
          <Sparkles size={20} />
        </div>
        <div className="min-w-0">
          <p className="text-xs uppercase tracking-[0.34em] text-[var(--color-ink-muted)]">
            Onyx Chat
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="truncate text-2xl font-semibold tracking-[-0.04em] text-white">
              {currentTitle}
            </h1>
            <button
              className="ui-button px-3 py-1.5 text-xs"
              onClick={() => {
                const next = window.prompt("Rename the current session", currentTitle);
                if (next) {
                  void renameCurrentSession(next);
                }
              }}
              type="button"
            >
              <Pencil size={14} />
              Rename
            </button>
          </div>
          <p className="mt-1 text-sm text-[var(--color-ink-soft)]">
            Local knowledge cockpit with live retrieval and file context.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-end gap-2.5">
        <button className="ui-button" onClick={() => void createNewSession()} type="button">
          <Plus size={16} />
          New session
        </button>
        <button
          className={ragMode ? "ui-button ui-button-primary" : "ui-button"}
          onClick={() => void toggleRagMode()}
          type="button"
        >
          <Database size={16} />
          {ragMode ? "RAG enabled" : "RAG disabled"}
        </button>
        <button
          className={skillRetrievalEnabled ? "ui-button ui-button-primary" : "ui-button"}
          onClick={() => void toggleSkillRetrieval()}
          type="button"
        >
          <FileSearch size={16} />
          {skillRetrievalEnabled ? "Skill on" : "Skill off"}
        </button>
        <div className="flex items-center gap-1 rounded-full border border-[var(--color-line)] bg-[var(--color-bg-soft)] p-1">
          <div className="flex items-center gap-2 rounded-full px-3 py-1 text-sm text-[var(--color-ink-soft)]">
            <Monitor size={15} />
            Shell
          </div>
          <button
            className={
              executionPlatform === "windows" ? "ui-button ui-button-primary px-3 py-1.5" : "ui-button border-transparent px-3 py-1.5"
            }
            onClick={() => void updateExecutionPlatform("windows")}
            type="button"
          >
            Win
          </button>
          <button
            className={
              executionPlatform === "linux" ? "ui-button ui-button-primary px-3 py-1.5" : "ui-button border-transparent px-3 py-1.5"
            }
            onClick={() => void updateExecutionPlatform("linux")}
            type="button"
          >
            Linux
          </button>
        </div>
        <button className="ui-button" onClick={() => void compressCurrentSession()} type="button">
          <Wrench size={16} />
          Compress
        </button>
        <button
          className={isIndexBuilding ? "ui-button" : "ui-button"}
          disabled={isIndexBuilding}
          onClick={() => void rebuildKnowledgeIndex()}
          type="button"
        >
          <FileSearch size={16} />
          {knowledgeIndexLabel}
        </button>
        <div className="ui-pill hidden md:inline-flex">
          <FileSearch size={14} />
          {knowledgeIndexHint}
        </div>
      </div>
    </header>
  );
}
