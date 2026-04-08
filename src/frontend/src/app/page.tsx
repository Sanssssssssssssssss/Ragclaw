"use client";

import dynamic from "next/dynamic";
import { RotateCcw } from "lucide-react";
import { useMemo, useState } from "react";

import { ChatInput } from "@/components/chat/ChatInput";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { Navbar } from "@/components/layout/Navbar";
import { AppProvider, useChatStore, useSessionStore } from "@/lib/store";

type WorkspaceView = "chat" | "trace" | "assets";

const TracePanel = dynamic(
  () => import("@/components/chat/TracePanel").then((module) => module.TracePanel),
  {
    loading: () => (
      <section className="panel flex min-h-[60vh] flex-1 items-center justify-center p-8 text-sm text-[var(--color-ink-soft)]">
        Loading trace view...
      </section>
    ),
    ssr: false
  }
);

const AssetsPanel = dynamic(
  () => import("@/components/chat/AssetsPanel").then((module) => module.AssetsPanel),
  {
    loading: () => (
      <section className="panel flex min-h-[60vh] flex-1 items-center justify-center p-8 text-sm text-[var(--color-ink-soft)]">
        Loading assets view...
      </section>
    ),
    ssr: false
  }
);

const Sidebar = dynamic(
  () => import("@/components/layout/Sidebar").then((module) => module.Sidebar),
  {
    loading: () => null,
    ssr: false
  }
);

const InspectorPanel = dynamic(
  () => import("@/components/editor/InspectorPanel").then((module) => module.InspectorPanel),
  {
    loading: () => null,
    ssr: false
  }
);

function WorkspaceBottomBar({
  workspaceView,
  onViewChange
}: {
  workspaceView: WorkspaceView;
  onViewChange: (view: WorkspaceView) => void;
}) {
  const { tokenStats, checkpoints, resumeCheckpoint, isStreaming } = useChatStore();
  const resumableCheckpoint = useMemo(
    () => checkpoints.find((item) => item.resume_eligible) ?? null,
    [checkpoints]
  );

  return (
    <div className="panel workspace-bottombar">
      <div className="workspace-tabs">
        <button
          className={workspaceView === "chat" ? "workspace-tab workspace-tab-active" : "workspace-tab"}
          onClick={() => onViewChange("chat")}
          type="button"
        >
          Chat
        </button>
        <button
          className={workspaceView === "trace" ? "workspace-tab workspace-tab-active" : "workspace-tab"}
          onClick={() => onViewChange("trace")}
          type="button"
        >
          Trace
        </button>
        <button
          className={workspaceView === "assets" ? "workspace-tab workspace-tab-active" : "workspace-tab"}
          onClick={() => onViewChange("assets")}
          type="button"
        >
          Assets
        </button>
        {workspaceView === "trace" && resumableCheckpoint ? (
          <button
            className="workspace-tab"
            disabled={isStreaming}
            onClick={() => void resumeCheckpoint(resumableCheckpoint.checkpoint_id)}
            type="button"
          >
            <span className="inline-flex items-center gap-2">
              <RotateCcw size={14} />
              Resume checkpoint
            </span>
          </button>
        ) : null}
      </div>

      <div className="pixel-stat">
        {tokenStats ? (
          <>
            <span>{`Model ${tokenStats.model_call_total_tokens.toLocaleString()} tokens`}</span>
            <span>{`Trace ${tokenStats.session_trace_tokens.toLocaleString()} tokens`}</span>
          </>
        ) : (
          <span>No token stats yet</span>
        )}
      </div>
    </div>
  );
}

function Workspace() {
  const [workspaceView, setWorkspaceView] = useState<WorkspaceView>("chat");
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  const { sendMessage, isStreaming, isInitializing, isSessionLoading, connectionError } = useChatStore();

  return (
    <main className="h-screen overflow-hidden px-3 py-3">
      <div className="mx-auto flex h-full min-h-0 w-full max-w-[1720px] flex-col gap-3">
        <Navbar onOpenFiles={() => setFilesOpen(true)} onOpenSessions={() => setSessionsOpen(true)} />

        <div className="flex min-h-0 flex-1 overflow-hidden">
          {workspaceView === "chat" ? <ChatPanel /> : workspaceView === "trace" ? <TracePanel /> : <AssetsPanel />}
        </div>

        {workspaceView === "chat" ? (
          <ChatInput
            disabled={isStreaming || isInitializing || isSessionLoading || Boolean(connectionError)}
            onSend={sendMessage}
          />
        ) : null}

        <WorkspaceBottomBar onViewChange={setWorkspaceView} workspaceView={workspaceView} />
      </div>

      {sessionsOpen ? <Sidebar onClose={() => setSessionsOpen(false)} /> : null}
      {filesOpen ? <InspectorPanel onClose={() => setFilesOpen(false)} /> : null}
    </main>
  );
}

export default function Page() {
  return (
    <AppProvider>
      <Workspace />
    </AppProvider>
  );
}
