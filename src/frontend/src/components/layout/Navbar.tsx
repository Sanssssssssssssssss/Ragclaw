"use client";

import { Database, FileText, Menu, Monitor, Pencil, Plus, Search, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useRuntimeStore, useSessionStore } from "@/lib/store";

export function Navbar({
  onOpenSessions,
  onOpenFiles
}: {
  onOpenSessions: () => void;
  onOpenFiles: () => void;
}) {
  const { currentSessionTitle, createNewSession, renameCurrentSession, compressCurrentSession } =
    useSessionStore();
  const {
    ragMode,
    toggleRagMode,
    skillRetrievalEnabled,
    toggleSkillRetrieval,
    executionPlatform,
    updateExecutionPlatform,
    knowledgeIndexStatus,
    rebuildKnowledgeIndex,
    runtimeLoading
  } = useRuntimeStore();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) {
      return;
    }

    const onPointerDown = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };

    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [menuOpen]);

  const knowledgeLabel = knowledgeIndexStatus?.building
    ? "Index rebuilding"
    : knowledgeIndexStatus?.ready
      ? `${knowledgeIndexStatus.indexed_files} files indexed`
      : "Index offline";

  return (
    <header className="panel workspace-topbar">
      <button aria-label="Open sessions" className="ui-button" onClick={onOpenSessions} type="button">
        <Menu size={16} />
        Threads
      </button>

      <div className="workspace-title-wrap">
        <p className="workspace-title-label">Current chat</p>
        <h1 className="workspace-title-text" title={currentSessionTitle}>
          {currentSessionTitle}
        </h1>
      </div>

      <div className="relative" ref={menuRef}>
        <button
          aria-label="Open tools"
          className="ui-button"
          onClick={() => setMenuOpen((value) => !value)}
          type="button"
        >
          <Wrench size={16} />
          Tools
        </button>

        {menuOpen ? (
          <div className="menu-popover absolute right-0 top-[calc(100%+0.75rem)] z-40 w-[420px] p-3">
            <div className="menu-section">
              <p className="menu-label">Session</p>
              <div className="grid gap-2">
                <button
                  className="menu-card"
                  onClick={() => {
                    void createNewSession();
                    setMenuOpen(false);
                  }}
                  type="button"
                >
                  <div>
                    <div className="menu-card-title">New session</div>
                    <div className="menu-card-copy">Start a fresh thread</div>
                  </div>
                  <Plus size={16} />
                </button>
                <button
                  className="menu-card"
                  onClick={() => {
                    const nextTitle = window.prompt("Rename the current session", currentSessionTitle);
                    if (nextTitle) {
                      void renameCurrentSession(nextTitle);
                    }
                    setMenuOpen(false);
                  }}
                  type="button"
                >
                  <div>
                    <div className="menu-card-title">Rename session</div>
                    <div className="menu-card-copy">{currentSessionTitle}</div>
                  </div>
                  <Pencil size={16} />
                </button>
                <button
                  className="menu-card"
                  onClick={() => {
                    void compressCurrentSession();
                    setMenuOpen(false);
                  }}
                  type="button"
                >
                  <div>
                    <div className="menu-card-title">Compress session</div>
                    <div className="menu-card-copy">Summarize older context</div>
                  </div>
                  <Wrench size={16} />
                </button>
                <button
                  className="menu-card"
                  onClick={() => {
                    onOpenFiles();
                    setMenuOpen(false);
                  }}
                  type="button"
                >
                  <div>
                    <div className="menu-card-title">Open files</div>
                    <div className="menu-card-copy">Browse and edit workspace files</div>
                  </div>
                  <FileText size={16} />
                </button>
              </div>
            </div>

            <div className="menu-section">
              <p className="menu-label">Runtime</p>
              <div className="grid gap-2 sm:grid-cols-2">
                <button
                  className="menu-card"
                  disabled={runtimeLoading}
                  onClick={() => void toggleRagMode()}
                  type="button"
                >
                  <div>
                    <div className="menu-card-title">RAG</div>
                    <div className="menu-card-copy">{ragMode ? "Enabled" : "Disabled"}</div>
                  </div>
                  <Search size={16} />
                </button>
                <button
                  className="menu-card"
                  disabled={runtimeLoading}
                  onClick={() => void toggleSkillRetrieval()}
                  type="button"
                >
                  <div>
                    <div className="menu-card-title">Skill retrieval</div>
                    <div className="menu-card-copy">
                      {skillRetrievalEnabled ? "Enabled" : "Disabled"}
                    </div>
                  </div>
                  <Search size={16} />
                </button>
              </div>
              <div className="menu-inline">
                <span className="menu-inline-label">
                  <Monitor size={14} />
                  Shell
                </span>
                <button
                  className={executionPlatform === "windows" ? "ui-button ui-button-primary" : "ui-button"}
                  onClick={() => void updateExecutionPlatform("windows")}
                  type="button"
                >
                  Windows
                </button>
                <button
                  className={executionPlatform === "linux" ? "ui-button ui-button-primary" : "ui-button"}
                  onClick={() => void updateExecutionPlatform("linux")}
                  type="button"
                >
                  Linux
                </button>
              </div>
            </div>

            <div className="menu-section">
              <p className="menu-label">Knowledge index</p>
              <div className="menu-note">{knowledgeLabel}</div>
              <button
                className="menu-card"
                disabled={Boolean(knowledgeIndexStatus?.building)}
                onClick={() => void rebuildKnowledgeIndex()}
                type="button"
              >
                <div>
                  <div className="menu-card-title">Rebuild index</div>
                  <div className="menu-card-copy">Refresh the current knowledge catalog</div>
                </div>
                <Database size={16} />
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </header>
  );
}
