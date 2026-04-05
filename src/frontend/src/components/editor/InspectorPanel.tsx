"use client";

import dynamic from "next/dynamic";
import { Save } from "lucide-react";
import { useEffect, useState } from "react";

import { useInspectorStore } from "@/lib/store";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false
});

const EDITOR_IDLE_TIMEOUT_MS = 1500;

/**
 * Returns one rendered inspector panel from no explicit inputs and provides file browsing plus inline editing.
 */
export function InspectorPanel() {
  const {
    editableFiles,
    inspectorPath,
    inspectorContent,
    inspectorDirty,
    loadInspectorFile,
    updateInspectorContent,
    saveInspector
  } = useInspectorStore();
  const [editorMounted, setEditorMounted] = useState(false);

  useEffect(() => {
    if (editorMounted) {
      return;
    }

    const activate = () => setEditorMounted(true);

    if (typeof window.requestIdleCallback === "function") {
      const idleId = window.requestIdleCallback(activate, { timeout: EDITOR_IDLE_TIMEOUT_MS });
      return () => window.cancelIdleCallback(idleId);
    }

    const timer = window.setTimeout(activate, 900);
    return () => window.clearTimeout(timer);
  }, [editorMounted]);

  return (
    <aside className="panel flex h-full min-h-0 flex-col rounded-[28px] p-4">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.3em] text-[var(--color-ink-muted)]">
            Inspector
          </p>
          <h2 className="text-xl font-semibold tracking-[-0.04em] text-white">Workspace files</h2>
        </div>
        <button className="ui-button" onClick={() => void saveInspector()} type="button">
          <Save size={16} />
          {inspectorDirty ? "Save changes" : "Synced"}
        </button>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {editableFiles.map((path) => (
          <button
            className={
              path === inspectorPath
                ? "ui-button ui-button-primary px-3 py-1.5 text-xs"
                : "ui-button px-3 py-1.5 text-xs"
            }
            key={path}
            onClick={() => {
              setEditorMounted(true);
              void loadInspectorFile(path);
            }}
            type="button"
          >
            {path}
          </button>
        ))}
      </div>

      <div
        className="min-h-0 flex-1 overflow-hidden rounded-[24px] border border-[var(--color-line)] bg-[#050505]"
        onPointerDown={() => setEditorMounted(true)}
      >
        {editorMounted ? (
          <MonacoEditor
            defaultLanguage="markdown"
            height="100%"
            loading={<div className="p-4 text-sm text-[var(--color-ink-soft)]">Loading editor...</div>}
            onChange={(value) => updateInspectorContent(value ?? "")}
            options={{
              automaticLayout: true,
              cursorBlinking: "solid",
              fontFamily: "var(--font-mono)",
              fontSize: 14,
              minimap: { enabled: false },
              overviewRulerBorder: false,
              renderLineHighlight: "none",
              scrollBeyondLastLine: false,
              smoothScrolling: true,
              wordWrap: "on"
            }}
            path={inspectorPath}
            theme="vs-dark"
            value={inspectorContent}
          />
        ) : (
          <div className="flex h-full flex-col gap-3 p-4">
            <div>
              <p className="text-sm text-[var(--color-ink-soft)]">
                Editor warmup is deferred so the chat surface stays smooth during startup and streaming.
              </p>
              <button
                className="ui-button mt-3"
                onClick={() => setEditorMounted(true)}
                type="button"
              >
                Load editor now
              </button>
            </div>
            <pre className="mono min-h-0 flex-1 overflow-auto rounded-[20px] border border-[var(--color-line)] bg-[rgba(255,255,255,0.03)] p-4 text-sm leading-7 text-[var(--color-ink-soft)]">
              {inspectorContent || "No file loaded yet."}
            </pre>
          </div>
        )}
      </div>
    </aside>
  );
}
