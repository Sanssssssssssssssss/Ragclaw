"use client";

import { useState } from "react";

import { ChatPanel } from "@/components/chat/ChatPanel";
import { TracePanel } from "@/components/chat/TracePanel";
import { InspectorPanel } from "@/components/editor/InspectorPanel";
import { Navbar } from "@/components/layout/Navbar";
import { ResizeHandle } from "@/components/layout/ResizeHandle";
import { Sidebar } from "@/components/layout/Sidebar";
import { AppProvider, useLayoutStore } from "@/lib/store";

type WorkspaceView = "chat" | "trace";

/**
 * Returns one rendered workspace from no explicit inputs and composes the viewport-constrained three-panel layout.
 */
function Workspace() {
  const { sidebarWidth, inspectorWidth, setSidebarWidth, setInspectorWidth } = useLayoutStore();
  const [workspaceView, setWorkspaceView] = useState<WorkspaceView>("chat");

  return (
    <main className="h-screen overflow-hidden px-3 py-3 md:px-4 md:py-4">
      <div className="mx-auto flex h-full w-full max-w-[1960px] flex-col gap-3">
        <Navbar currentView={workspaceView} onViewChange={setWorkspaceView} />
        <div className="flex min-h-0 flex-1 gap-0 overflow-hidden">
          <div className="min-h-0" style={{ width: sidebarWidth }}>
            <Sidebar />
          </div>
          <ResizeHandle onResize={(delta) => setSidebarWidth(Math.max(240, sidebarWidth + delta))} />
          {workspaceView === "chat" ? <ChatPanel /> : <TracePanel />}
          <ResizeHandle
            onResize={(delta) => setInspectorWidth(Math.max(300, inspectorWidth - delta))}
          />
          <div className="min-h-0" style={{ width: inspectorWidth }}>
            <InspectorPanel />
          </div>
        </div>
      </div>
    </main>
  );
}

/**
 * Returns the application root page from no explicit inputs and mounts the shared app store provider.
 */
export default function Page() {
  return (
    <AppProvider>
      <Workspace />
    </AppProvider>
  );
}
