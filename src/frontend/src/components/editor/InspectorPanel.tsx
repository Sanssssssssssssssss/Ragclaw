"use client";

import dynamic from "next/dynamic";
import { Loader2, Save, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { VirtualizedStack } from "@/components/chat/VirtualizedStack";
import { useInspectorStore } from "@/lib/store";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false
});

const FILE_ROW_ESTIMATE = 52;

function getLanguage(path: string | null) {
  if (!path) return "markdown";
  if (path.endsWith(".ts") || path.endsWith(".tsx")) return "typescript";
  if (path.endsWith(".js") || path.endsWith(".jsx")) return "javascript";
  if (path.endsWith(".json")) return "json";
  if (path.endsWith(".md")) return "markdown";
  if (path.endsWith(".py")) return "python";
  if (path.endsWith(".yml") || path.endsWith(".yaml")) return "yaml";
  return "plaintext";
}

export function InspectorPanel({ onClose }: { onClose: () => void }) {
  const {
    editableFiles,
    inspectorPath,
    inspectorContent,
    inspectorDirty,
    inspectorCatalogReady,
    inspectorCatalogLoading,
    inspectorFileLoading,
    inspectorSaving,
    ensureInspectorCatalog,
    loadInspectorFile,
    updateInspectorContent,
    saveInspector
  } = useInspectorStore();
  const [query, setQuery] = useState("");

  useEffect(() => {
    void ensureInspectorCatalog();
  }, [ensureInspectorCatalog]);

  const filteredFiles = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) {
      return editableFiles;
    }

    return editableFiles.filter((path) => path.toLowerCase().includes(normalized));
  }, [editableFiles, query]);

  return (
    <div className="drawer-root">
      <button aria-label="Close files drawer" className="drawer-backdrop" onClick={onClose} type="button" />
      <aside className="drawer-panel drawer-panel-right">
        <div className="flex items-start justify-between gap-3 border-b border-[var(--color-line)] px-5 py-5">
          <div>
            <p className="pixel-label">
              # Files
            </p>
            <h2 className="pixel-title mt-2 text-[0.92rem] text-[var(--color-ink)]">Workspace editor</h2>
          </div>

          <div className="flex items-center gap-2">
            <button
              className="ui-button"
              disabled={!inspectorPath || inspectorSaving}
              onClick={() => void saveInspector()}
              type="button"
            >
              {inspectorSaving ? <Loader2 className="animate-spin" size={15} /> : <Save size={15} />}
              {inspectorDirty ? "Save" : "Saved"}
            </button>
            <button className="ui-button" onClick={onClose} type="button">
              <X size={16} />
              Close
            </button>
          </div>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)]">
          <div className="border-r border-[var(--color-line)] px-4 py-4">
            <label className="relative block">
              <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-ink-muted)]" size={16} />
              <input
                className="pixel-field py-2 pl-10 pr-4 text-sm"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="# FILTER FILES"
                value={query}
              />
            </label>

            <div className="mt-4 h-[calc(100vh-17rem)] min-h-0">
              {inspectorCatalogLoading && !inspectorCatalogReady ? (
                <div className="pixel-card-soft px-4 py-3 text-sm text-[var(--color-ink-soft)]">
                  Loading file catalog...
                </div>
              ) : (
                <VirtualizedStack
                  className="h-full overflow-y-auto pr-1"
                  estimateHeight={FILE_ROW_ESTIMATE}
                  getKey={(path) => path}
                  items={filteredFiles}
                  renderItem={(path) => (
                    <div className="pb-2">
                      <button
                        className={`w-full px-3 py-2 text-left text-sm transition ${
                          path === inspectorPath
                            ? "pixel-card-soft text-[var(--color-ink)]"
                            : "pixel-card text-[var(--color-ink-soft)]"
                        }`}
                        onClick={() => void loadInspectorFile(path)}
                        type="button"
                      >
                        {path}
                      </button>
                    </div>
                  )}
                />
              )}
            </div>
          </div>

          <div className="min-h-0 px-4 py-4">
            {!inspectorPath ? (
              <div className="pixel-card-soft flex h-full items-center justify-center px-6 text-center text-sm leading-7 text-[var(--color-ink-soft)]">
                Select a file to load its contents. Monaco and file payloads stay out of the startup path until you open this drawer and choose a file.
              </div>
            ) : inspectorFileLoading ? (
              <div className="pixel-card flex h-full items-center justify-center text-sm text-[var(--color-ink-soft)]">
                Loading file...
              </div>
            ) : (
              <div className="pixel-card h-full overflow-hidden bg-[#0b0b0b]">
                <MonacoEditor
                  defaultLanguage={getLanguage(inspectorPath)}
                  height="100%"
                  loading={<div className="p-4 text-sm text-[var(--color-ink-soft)]">Loading editor...</div>}
                  onChange={(value) => updateInspectorContent(value ?? "")}
                  options={{
                    automaticLayout: true,
                    fontFamily: "var(--font-mono)",
                    fontSize: 14,
                    minimap: { enabled: false },
                    renderLineHighlight: "none",
                    scrollBeyondLastLine: false,
                    smoothScrolling: true,
                    wordWrap: "on"
                  }}
                  path={inspectorPath}
                  theme="vs-dark"
                  value={inspectorContent}
                />
              </div>
            )}
          </div>
        </div>
      </aside>
    </div>
  );
}
