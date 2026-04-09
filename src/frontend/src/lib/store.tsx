"use client";

import {
  createContext,
  startTransition,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode
} from "react";

import {
  ApiConnectionError,
  compressSession,
  createSession,
  deleteSession,
  getExecutionPlatform,
  getKnowledgeIndexStatus,
  getPendingHitl,
  getRagMode,
  getSessionContext,
  getSessionHistory,
  getSessionTokens,
  getSkillRetrieval,
  listSessionCheckpoints,
  listSessions,
  listSkills,
  listMcpCapabilities,
  loadFile,
  renameSession,
  rebuildKnowledgeIndex as rebuildKnowledgeIndexRequest,
  saveFile,
  setExecutionPlatform as setExecutionPlatformRequest,
  setRagMode,
  setSkillRetrieval,
  streamChat,
  streamCheckpointResume,
  streamHitlDecision,
  triggerContextConsolidation,
  type CheckpointEvent,
  type CheckpointSummary,
  type Evidence,
  type ExecutionPlatform,
  type HitlAuditEntry,
  type HitlEvent,
  type KnowledgeIndexStatus,
  type McpCapabilitySummary,
  type MessageUsage,
  type PendingHitlInterrupt,
  type SessionContextPayload,
  type RetrievalStep,
  type RunMeta,
  type RunStatus,
  type SessionSummary,
  type SessionTokenStats,
  type ToolCall
} from "@/lib/api";

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  retrievalSteps: RetrievalStep[];
  usage: MessageUsage | null;
  runMeta: RunMeta | null;
  checkpointEvents: CheckpointEvent[];
  hitlEvents: HitlEvent[];
};

type SessionStore = {
  sessions: SessionSummary[];
  currentSessionId: string | null;
  currentSessionTitle: string;
  createNewSession: () => Promise<void>;
  selectSession: (sessionId: string) => Promise<void>;
  renameCurrentSession: (title: string) => Promise<void>;
  removeSession: (sessionId: string) => Promise<void>;
  compressCurrentSession: () => Promise<void>;
};

type ChatStore = {
  messages: Message[];
  streamingMessages: Message[];
  checkpoints: CheckpointSummary[];
  pendingHitl: PendingHitlInterrupt | null;
  hitlAudit: HitlAuditEntry[];
  mcpCapabilities: McpCapabilitySummary[];
  sessionContext: SessionContextPayload | null;
  isInitializing: boolean;
  isSessionLoading: boolean;
  isStreaming: boolean;
  assetsLoading: boolean;
  connectionError: string | null;
  tokenStats: SessionTokenStats | null;
  retryInitialization: () => Promise<void>;
  sendMessage: (value: string) => Promise<void>;
  resumeCheckpoint: (checkpointId: string) => Promise<void>;
  submitHitlDecision: (
    checkpointId: string,
    decision: "approve" | "reject" | "edit",
    editedInput?: Record<string, unknown>
  ) => Promise<void>;
  refreshCheckpoints: () => Promise<void>;
  refreshAssets: () => Promise<void>;
  triggerConsolidation: () => Promise<void>;
};

type RuntimeStore = {
  ragMode: boolean;
  skillRetrievalEnabled: boolean;
  executionPlatform: ExecutionPlatform;
  knowledgeIndexStatus: KnowledgeIndexStatus | null;
  runtimeReady: boolean;
  runtimeLoading: boolean;
  toggleRagMode: () => Promise<void>;
  toggleSkillRetrieval: () => Promise<void>;
  updateExecutionPlatform: (platform: ExecutionPlatform) => Promise<void>;
  rebuildKnowledgeIndex: () => Promise<void>;
  refreshRuntime: () => Promise<void>;
};

type InspectorStore = {
  skills: Array<{ name: string; description: string; path: string }>;
  editableFiles: string[];
  inspectorPath: string | null;
  inspectorContent: string;
  inspectorDirty: boolean;
  inspectorCatalogReady: boolean;
  inspectorCatalogLoading: boolean;
  inspectorFileLoading: boolean;
  inspectorSaving: boolean;
  ensureInspectorCatalog: () => Promise<void>;
  loadInspectorFile: (path: string) => Promise<void>;
  updateInspectorContent: (value: string) => void;
  saveInspector: () => Promise<void>;
};

const SessionContext = createContext<SessionStore | null>(null);
const ChatContext = createContext<ChatStore | null>(null);
const RuntimeContext = createContext<RuntimeStore | null>(null);
const InspectorContext = createContext<InspectorStore | null>(null);

const STREAM_TOKEN_FLUSH_MS = 90;
const FIXED_FILES = [
  "workspace/SOUL.md",
  "workspace/IDENTITY.md",
  "workspace/USER.md",
  "workspace/AGENTS.md",
  "memory/MEMORY.md",
  "SKILLS_SNAPSHOT.md"
];

const makeId = () => `${Date.now()}-${Math.random().toString(16).slice(2)}`;

function normalizeEvidence(value: unknown): Evidence | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const rawScore = item.score;
  const score =
    typeof rawScore === "number"
      ? rawScore
      : typeof rawScore === "string" && rawScore.trim()
        ? Number(rawScore)
        : null;
  return {
    source_path: String(item.source_path ?? ""),
    source_type: String(item.source_type ?? ""),
    locator: String(item.locator ?? ""),
    snippet: String(item.snippet ?? ""),
    channel: (item.channel as Evidence["channel"]) ?? "skill",
    score: Number.isFinite(score) ? score : null,
    parent_id: item.parent_id ? String(item.parent_id) : null
  };
}

function normalizeRetrievalStep(value: unknown): RetrievalStep | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  return {
    kind: item.kind === "memory" ? "memory" : "knowledge",
    stage: String(item.stage ?? "unknown"),
    title: String(item.title ?? "Retrieval results"),
    message: String(item.message ?? ""),
    results: (Array.isArray(item.results) ? item.results : [])
      .map((entry) => normalizeEvidence(entry))
      .filter((entry): entry is Evidence => entry !== null)
  };
}

function normalizeUsage(value: unknown): MessageUsage | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const inputTokens = Number(item.input_tokens ?? 0);
  const outputTokens = Number(item.output_tokens ?? 0);
  if (!Number.isFinite(inputTokens) || !Number.isFinite(outputTokens)) return null;
  return { input_tokens: inputTokens, output_tokens: outputTokens };
}

function normalizeRunStatus(value: unknown): RunStatus {
  return value === "fresh" ||
    value === "resumed" ||
    value === "interrupted" ||
    value === "restoring" ||
    value === "edited" ||
    value === "awaiting_approval"
    ? value
    : "fresh";
}

function normalizeRunMeta(value: unknown): RunMeta | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  return {
    status: normalizeRunStatus(item.status),
    thread_id: String(item.thread_id ?? ""),
    checkpoint_id: String(item.checkpoint_id ?? ""),
    resume_source: String(item.resume_source ?? ""),
    orchestration_engine: String(item.orchestration_engine ?? "")
  };
}

function normalizeCheckpointEvent(value: unknown): CheckpointEvent | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const type = item.type;
  if (type !== "created" && type !== "resumed" && type !== "interrupted") return null;
  return {
    type,
    checkpoint_id: String(item.checkpoint_id ?? ""),
    thread_id: String(item.thread_id ?? ""),
    resume_source: String(item.resume_source ?? ""),
    state_label: String(item.state_label ?? ""),
    created_at: String(item.created_at ?? ""),
    orchestration_engine: String(item.orchestration_engine ?? "")
  };
}

function normalizeHitlEvent(value: unknown): HitlEvent | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const type = item.type;
  if (type !== "requested" && type !== "approved" && type !== "rejected" && type !== "edited") return null;
  return {
    type,
    request_id: String(item.request_id ?? ""),
    requested_at: String(item.requested_at ?? ""),
    decision_id: String(item.decision_id ?? ""),
    decision: String(item.decision ?? ""),
    actor_id: String(item.actor_id ?? ""),
    actor_type: String(item.actor_type ?? ""),
    decided_at: String(item.decided_at ?? ""),
    run_id: String(item.run_id ?? ""),
    thread_id: String(item.thread_id ?? ""),
    session_id: String(item.session_id ?? ""),
    capability_id: String(item.capability_id ?? ""),
    capability_type: String(item.capability_type ?? ""),
    display_name: String(item.display_name ?? ""),
    risk_level: String(item.risk_level ?? ""),
    reason: String(item.reason ?? ""),
    proposed_input:
      item.proposed_input && typeof item.proposed_input === "object"
        ? (item.proposed_input as Record<string, unknown>)
        : {},
    approved_input_snapshot:
      item.approved_input_snapshot && typeof item.approved_input_snapshot === "object"
        ? (item.approved_input_snapshot as Record<string, unknown>)
        : null,
    edited_input_snapshot:
      item.edited_input_snapshot && typeof item.edited_input_snapshot === "object"
        ? (item.edited_input_snapshot as Record<string, unknown>)
        : null,
    rejected_input_snapshot:
      item.rejected_input_snapshot && typeof item.rejected_input_snapshot === "object"
        ? (item.rejected_input_snapshot as Record<string, unknown>)
        : null,
    checkpoint_id: String(item.checkpoint_id ?? ""),
    resume_source: String(item.resume_source ?? ""),
    orchestration_engine: String(item.orchestration_engine ?? "")
  };
}

function normalizePendingHitl(value: unknown): PendingHitlInterrupt | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  return {
    request_id: String(item.request_id ?? ""),
    run_id: String(item.run_id ?? ""),
    thread_id: String(item.thread_id ?? ""),
    session_id: item.session_id == null ? null : String(item.session_id),
    capability_id: String(item.capability_id ?? ""),
    capability_type: String(item.capability_type ?? ""),
    display_name: String(item.display_name ?? ""),
    risk_level: String(item.risk_level ?? ""),
    reason: String(item.reason ?? ""),
    proposed_input:
      item.proposed_input && typeof item.proposed_input === "object"
        ? (item.proposed_input as Record<string, unknown>)
        : {},
    requested_at: String(item.requested_at ?? ""),
    status: String(item.status ?? ""),
    checkpoint_id: String(item.checkpoint_id ?? "")
  };
}

function normalizeHitlAuditEntry(value: unknown): HitlAuditEntry | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const request = normalizePendingHitl(item.request);
  if (!request) return null;
  const rawDecision = item.decision;
  const decision =
    rawDecision && typeof rawDecision === "object"
      ? {
          decision_id: String((rawDecision as Record<string, unknown>).decision_id ?? ""),
          request_id: String((rawDecision as Record<string, unknown>).request_id ?? ""),
          decision: String((rawDecision as Record<string, unknown>).decision ?? ""),
          actor_id: String((rawDecision as Record<string, unknown>).actor_id ?? ""),
          actor_type: String((rawDecision as Record<string, unknown>).actor_type ?? ""),
          decided_at: String((rawDecision as Record<string, unknown>).decided_at ?? ""),
          resume_source: String((rawDecision as Record<string, unknown>).resume_source ?? ""),
          approved_input_snapshot:
            (rawDecision as Record<string, unknown>).approved_input_snapshot &&
            typeof (rawDecision as Record<string, unknown>).approved_input_snapshot === "object"
              ? ((rawDecision as Record<string, unknown>).approved_input_snapshot as Record<string, unknown>)
              : null,
          edited_input_snapshot:
            (rawDecision as Record<string, unknown>).edited_input_snapshot &&
            typeof (rawDecision as Record<string, unknown>).edited_input_snapshot === "object"
              ? ((rawDecision as Record<string, unknown>).edited_input_snapshot as Record<string, unknown>)
              : null,
          rejected_input_snapshot:
            (rawDecision as Record<string, unknown>).rejected_input_snapshot &&
            typeof (rawDecision as Record<string, unknown>).rejected_input_snapshot === "object"
              ? ((rawDecision as Record<string, unknown>).rejected_input_snapshot as Record<string, unknown>)
              : null,
        }
      : null;
  return { request, decision };
}

function toUiMessages(history: Awaited<ReturnType<typeof getSessionHistory>>["messages"]) {
  return history.map((message) => ({
    id: makeId(),
    role: message.role,
    content: message.content ?? "",
    toolCalls: message.tool_calls ?? [],
    retrievalSteps: (message.retrieval_steps ?? [])
      .map((step) => normalizeRetrievalStep(step))
      .filter((step): step is RetrievalStep => step !== null),
    usage: normalizeUsage(message.usage),
    runMeta: normalizeRunMeta(message.run_meta),
    checkpointEvents: (message.checkpoint_events ?? [])
      .map((item) => normalizeCheckpointEvent(item))
      .filter((item): item is CheckpointEvent => item !== null),
    hitlEvents: (message.hitl_events ?? [])
      .map((item) => normalizeHitlEvent(item))
      .filter((item): item is HitlEvent => item !== null)
  }));
}

function toErrorMessage(error: unknown) {
  if (error instanceof ApiConnectionError) {
    return `${error.message} If you started the app with backend/scripts/dev/start-dev.ps1, wait for the backend to finish booting and try again.`;
  }
  if (error instanceof Error && error.message.trim()) return error.message.trim();
  return "An unexpected error occurred while talking to the backend.";
}

function updateMessageAtPosition(
  previous: Message[],
  preferredIndex: number,
  messageId: string,
  updater: (message: Message) => Message
) {
  const preferredMatch =
    preferredIndex >= 0 &&
    preferredIndex < previous.length &&
    previous[preferredIndex]?.id === messageId;
  const targetIndex = preferredMatch
    ? preferredIndex
    : previous.findIndex((message) => message.id === messageId);
  if (targetIndex === -1) return previous;
  const next = [...previous];
  next[targetIndex] = updater(previous[targetIndex]);
  return next;
}

const createAssistantDraft = (runMeta: RunMeta | null): Message => ({
  id: makeId(),
  role: "assistant",
  content: "",
  toolCalls: [],
  retrievalSteps: [],
  usage: null,
  runMeta,
  checkpointEvents: [],
  hitlEvents: []
});

const assistantHasPayload = (message: Message) =>
  message.content.trim().length > 0 ||
  message.toolCalls.length > 0 ||
  message.retrievalSteps.length > 0 ||
  message.checkpointEvents.length > 0 ||
  message.hitlEvents.length > 0 ||
  message.usage !== null;

const buildPendingHitlFromEvent = (event: HitlEvent): PendingHitlInterrupt => ({
  request_id: event.request_id,
  run_id: event.run_id,
  thread_id: event.thread_id,
  session_id: event.session_id || null,
  capability_id: event.capability_id,
  capability_type: event.capability_type,
  display_name: event.display_name,
  risk_level: event.risk_level,
  reason: event.reason,
  proposed_input: event.proposed_input,
  requested_at: event.requested_at,
  status: event.type === "requested" ? "pending" : event.decision || event.type,
  checkpoint_id: event.checkpoint_id
});

const buildEditableFiles = (skills: Array<{ name: string; description: string; path: string }>) => {
  const files = [...FIXED_FILES];
  for (const skill of skills) {
    if (!files.includes(skill.path)) files.push(skill.path);
  }
  return files;
};

export function AppProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streamingMessagesState, setStreamingMessagesState] = useState<Message[]>([]);
  const [checkpoints, setCheckpoints] = useState<CheckpointSummary[]>([]);
  const [pendingHitl, setPendingHitl] = useState<PendingHitlInterrupt | null>(null);
  const [hitlAudit, setHitlAudit] = useState<HitlAuditEntry[]>([]);
  const [mcpCapabilities, setMcpCapabilities] = useState<McpCapabilitySummary[]>([]);
  const [sessionContext, setSessionContext] = useState<SessionContextPayload | null>(null);
  const [isInitializing, setIsInitializing] = useState(true);
  const [isSessionLoading, setIsSessionLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [assetsLoading, setAssetsLoading] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [tokenStats, setTokenStats] = useState<SessionTokenStats | null>(null);
  const [ragMode, setRagModeState] = useState(false);
  const [skillRetrievalEnabled, setSkillRetrievalEnabled] = useState(true);
  const [executionPlatform, setExecutionPlatformState] = useState<ExecutionPlatform>("windows");
  const [knowledgeIndexStatus, setKnowledgeIndexStatus] = useState<KnowledgeIndexStatus | null>(null);
  const [runtimeReady, setRuntimeReady] = useState(false);
  const [runtimeLoading, setRuntimeLoading] = useState(false);
  const [skills, setSkills] = useState<Array<{ name: string; description: string; path: string }>>([]);
  const [inspectorPath, setInspectorPath] = useState<string | null>(null);
  const [inspectorContent, setInspectorContent] = useState("");
  const [inspectorDirty, setInspectorDirty] = useState(false);
  const [inspectorCatalogReady, setInspectorCatalogReady] = useState(false);
  const [inspectorCatalogLoading, setInspectorCatalogLoading] = useState(false);
  const [inspectorFileLoading, setInspectorFileLoading] = useState(false);
  const [inspectorSaving, setInspectorSaving] = useState(false);

  const streamingMessagesRef = useRef<Message[]>([]);
  const sessionLoadVersionRef = useRef(0);
  const runtimeRequestRef = useRef(0);

  const editableFiles = useMemo(() => buildEditableFiles(skills), [skills]);
  const currentSessionTitle = useMemo(
    () => sessions.find((session) => session.id === currentSessionId)?.title ?? "New session",
    [currentSessionId, sessions]
  );

  const setStreamingMessages = useCallback(
    (value: Message[] | ((previous: Message[]) => Message[])) => {
      setStreamingMessagesState((previous) => {
        const next = typeof value === "function" ? value(previous) : value;
        streamingMessagesRef.current = next;
        return next;
      });
    },
    []
  );

  const refreshSessions = useCallback(async () => {
    const nextSessions = await listSessions();
    setSessions(nextSessions);
    return nextSessions;
  }, []);

  const refreshSessionTokens = useCallback(async (sessionId: string) => {
    setTokenStats(await getSessionTokens(sessionId));
  }, []);

  const refreshCheckpoints = useCallback(
      async (sessionId?: string | null) => {
      const resolvedSessionId = sessionId ?? currentSessionId;
      setAssetsLoading(true);
        try {
          if (!resolvedSessionId) {
            setCheckpoints([]);
            setPendingHitl(null);
            setHitlAudit([]);
            setSessionContext(null);
            const mcpPayload = await listMcpCapabilities();
            setMcpCapabilities(mcpPayload.capabilities);
            return;
          }
          const [checkpointPayload, pendingPayload, mcpPayload, contextPayload] = await Promise.all([
            listSessionCheckpoints(resolvedSessionId),
            getPendingHitl(resolvedSessionId),
            listMcpCapabilities(),
            getSessionContext(resolvedSessionId)
          ]);
          setCheckpoints(checkpointPayload.checkpoints);
          setPendingHitl(normalizePendingHitl(pendingPayload.pending_interrupt));
          setHitlAudit(
            (Array.isArray(pendingPayload.requests) ? pendingPayload.requests : [])
              .map((item) => normalizeHitlAuditEntry(item))
              .filter((item): item is HitlAuditEntry => item !== null)
          );
          setMcpCapabilities(Array.isArray(mcpPayload.capabilities) ? mcpPayload.capabilities : []);
          setSessionContext(contextPayload);
        } finally {
          setAssetsLoading(false);
        }
    },
      [currentSessionId]
    );

  const triggerConsolidation = useCallback(async () => {
    if (!currentSessionId) return;
    setAssetsLoading(true);
    try {
      await triggerContextConsolidation(currentSessionId);
      await refreshCheckpoints(currentSessionId);
      setConnectionError(null);
    } catch (error) {
      setConnectionError(toErrorMessage(error));
    } finally {
      setAssetsLoading(false);
    }
  }, [currentSessionId, refreshCheckpoints]);

  const loadSessionEssentials = useCallback(
    async (sessionId: string) => {
      const loadVersion = ++sessionLoadVersionRef.current;
        const [history, tokens, checkpointPayload, pendingPayload, mcpPayload, contextPayload] = await Promise.all([
          getSessionHistory(sessionId),
          getSessionTokens(sessionId),
          listSessionCheckpoints(sessionId),
          getPendingHitl(sessionId),
          listMcpCapabilities(),
          getSessionContext(sessionId)
        ]);
        if (loadVersion !== sessionLoadVersionRef.current) return;
        setMessages(toUiMessages(history.messages));
        setStreamingMessages([]);
        setTokenStats(tokens);
      setCheckpoints(checkpointPayload.checkpoints);
      setPendingHitl(normalizePendingHitl(pendingPayload.pending_interrupt));
        setHitlAudit(
          (Array.isArray(pendingPayload.requests) ? pendingPayload.requests : [])
            .map((item) => normalizeHitlAuditEntry(item))
            .filter((item): item is HitlAuditEntry => item !== null)
        );
        setMcpCapabilities(Array.isArray(mcpPayload.capabilities) ? mcpPayload.capabilities : []);
        setSessionContext(contextPayload);
      },
    [setStreamingMessages]
  );

  const refreshRuntime = useCallback(async () => {
    const requestId = ++runtimeRequestRef.current;
    setRuntimeLoading(true);
    try {
      const [rag, skillRetrieval, platform, indexStatus] = await Promise.all([
        getRagMode(),
        getSkillRetrieval(),
        getExecutionPlatform(),
        getKnowledgeIndexStatus()
      ]);
      if (requestId !== runtimeRequestRef.current) return;
      setRagModeState(rag.enabled);
      setSkillRetrievalEnabled(skillRetrieval.enabled);
      setExecutionPlatformState(platform.platform);
      setKnowledgeIndexStatus(indexStatus);
      setRuntimeReady(true);
      setConnectionError(null);
    } catch (error) {
      if (requestId === runtimeRequestRef.current) setConnectionError(toErrorMessage(error));
    } finally {
      if (requestId === runtimeRequestRef.current) setRuntimeLoading(false);
    }
  }, []);

  const ensureInspectorCatalog = useCallback(async () => {
    if (inspectorCatalogReady || inspectorCatalogLoading) return;
    setInspectorCatalogLoading(true);
    try {
      setSkills(await listSkills());
      setInspectorCatalogReady(true);
      setConnectionError(null);
    } catch (error) {
      setConnectionError(toErrorMessage(error));
    } finally {
      setInspectorCatalogLoading(false);
    }
  }, [inspectorCatalogLoading, inspectorCatalogReady]);

  const bootstrapCore = useCallback(async () => {
    setIsInitializing(true);
    setIsSessionLoading(false);
    setConnectionError(null);
    setRuntimeReady(false);
    try {
      const initialSessions = await listSessions();
      let nextSessions = initialSessions;
      let nextSessionId = initialSessions[0]?.id ?? null;
      if (!nextSessionId) {
        const created = await createSession();
        nextSessions = [created];
        nextSessionId = created.id;
      }
      setSessions(nextSessions);
      setCurrentSessionId(nextSessionId);
      if (nextSessionId) {
        await loadSessionEssentials(nextSessionId);
      } else {
        setMessages([]);
        setStreamingMessages([]);
        setCheckpoints([]);
        setPendingHitl(null);
        setSessionContext(null);
        setTokenStats(null);
      }
    } catch (error) {
      setConnectionError(toErrorMessage(error));
      setSessions([]);
      setCurrentSessionId(null);
      setMessages([]);
      setStreamingMessages([]);
      setCheckpoints([]);
      setPendingHitl(null);
      setHitlAudit([]);
      setSessionContext(null);
      setMcpCapabilities([]);
      setTokenStats(null);
      setKnowledgeIndexStatus(null);
      setSkills([]);
      setInspectorPath(null);
      setInspectorContent("");
      setInspectorDirty(false);
      setInspectorCatalogReady(false);
    } finally {
      setIsInitializing(false);
    }
  }, [loadSessionEssentials, setStreamingMessages]);

  const retryInitialization = useCallback(async () => {
    await bootstrapCore();
    await refreshRuntime();
  }, [bootstrapCore, refreshRuntime]);

  useEffect(() => {
    void bootstrapCore();
  }, [bootstrapCore]);

  useEffect(() => {
    if (isInitializing) return;
    let timer: number | null = null;
    let idleId: number | null = null;
    const run = () => void refreshRuntime();
    const hasIdleCallback =
      typeof window !== "undefined" && typeof window.requestIdleCallback === "function";
    if (hasIdleCallback) {
      idleId = window.requestIdleCallback(run, { timeout: 1200 });
    } else {
      timer = window.setTimeout(run, 280);
    }
    return () => {
      if (timer !== null) window.clearTimeout(timer);
      if (
        idleId !== null &&
        typeof window !== "undefined" &&
        typeof window.cancelIdleCallback === "function"
      ) {
        window.cancelIdleCallback(idleId);
      }
    };
  }, [isInitializing, refreshRuntime]);

  useEffect(() => {
    if (!knowledgeIndexStatus?.building) return;
    const timer = window.setInterval(() => {
      void getKnowledgeIndexStatus().then((status) => setKnowledgeIndexStatus(status));
    }, 3000);
    return () => window.clearInterval(timer);
  }, [knowledgeIndexStatus?.building]);

  const ensureSession = useCallback(async () => {
    if (currentSessionId) return currentSessionId;
    const created = await createSession();
    setSessions((previous) => [created, ...previous]);
    setCurrentSessionId(created.id);
    setMessages([]);
    setStreamingMessages([]);
    setCheckpoints([]);
    setPendingHitl(null);
    setSessionContext(null);
    setTokenStats(null);
    return created.id;
  }, [currentSessionId, setStreamingMessages]);

  const createNewSession = useCallback(async () => {
    try {
      const created = await createSession();
      setCurrentSessionId(created.id);
      setSessions((previous) => [created, ...previous]);
        setMessages([]);
        setStreamingMessages([]);
        setCheckpoints([]);
        setPendingHitl(null);
        setSessionContext(null);
        setTokenStats(null);
        setConnectionError(null);
      void refreshSessions();
    } catch (error) {
      setConnectionError(toErrorMessage(error));
    }
  }, [refreshSessions, setStreamingMessages]);

  const selectSession = useCallback(
    async (sessionId: string) => {
      if (!sessionId || isStreaming) return;
      setCurrentSessionId(sessionId);
        setMessages([]);
        setStreamingMessages([]);
        setCheckpoints([]);
        setPendingHitl(null);
        setSessionContext(null);
        setTokenStats(null);
        setIsSessionLoading(true);
      setConnectionError(null);
      try {
        await loadSessionEssentials(sessionId);
      } catch (error) {
        setConnectionError(toErrorMessage(error));
      } finally {
        setIsSessionLoading(false);
      }
    },
    [isStreaming, loadSessionEssentials, setStreamingMessages]
  );

  const renameCurrentSession = useCallback(
    async (title: string) => {
      if (!currentSessionId || !title.trim()) return;
      try {
        await renameSession(currentSessionId, title.trim());
        await refreshSessions();
        setConnectionError(null);
      } catch (error) {
        setConnectionError(toErrorMessage(error));
      }
    },
    [currentSessionId, refreshSessions]
  );

  const removeSession = useCallback(
    async (sessionId: string) => {
      try {
        await deleteSession(sessionId);
        const nextSessions = await refreshSessions();
        if (currentSessionId !== sessionId) return;
        const nextSessionId = nextSessions[0]?.id ?? null;
        setCurrentSessionId(nextSessionId);
        if (!nextSessionId) {
            setMessages([]);
            setStreamingMessages([]);
            setCheckpoints([]);
            setPendingHitl(null);
            setSessionContext(null);
            setTokenStats(null);
            return;
        }
        setIsSessionLoading(true);
        setMessages([]);
        setStreamingMessages([]);
        setCheckpoints([]);
        setPendingHitl(null);
        setSessionContext(null);
        setTokenStats(null);
        try {
          await loadSessionEssentials(nextSessionId);
        } finally {
          setIsSessionLoading(false);
        }
      } catch (error) {
        setConnectionError(toErrorMessage(error));
      }
    },
    [currentSessionId, loadSessionEssentials, refreshSessions, setStreamingMessages]
  );

  const loadInspectorFile = useCallback(async (path: string) => {
    setInspectorFileLoading(true);
    try {
      const file = await loadFile(path);
      setInspectorPath(file.path);
      setInspectorContent(file.content);
      setInspectorDirty(false);
      setConnectionError(null);
    } catch (error) {
      setConnectionError(toErrorMessage(error));
    } finally {
      setInspectorFileLoading(false);
    }
  }, []);

  const updateInspectorContent = useCallback((value: string) => {
    setInspectorContent(value);
    setInspectorDirty(true);
  }, []);

  const saveInspector = useCallback(async () => {
    if (!inspectorPath) return;
    setInspectorSaving(true);
    try {
      await saveFile(inspectorPath, inspectorContent);
      if (inspectorCatalogReady) setSkills(await listSkills());
      setInspectorDirty(false);
      setConnectionError(null);
    } catch (error) {
      setConnectionError(toErrorMessage(error));
    } finally {
      setInspectorSaving(false);
    }
  }, [inspectorCatalogReady, inspectorContent, inspectorPath]);

  const compressCurrentSession = useCallback(async () => {
    if (!currentSessionId) return;
    setIsSessionLoading(true);
    try {
      await compressSession(currentSessionId);
      await loadSessionEssentials(currentSessionId);
      await refreshSessions();
      setConnectionError(null);
    } catch (error) {
      setConnectionError(toErrorMessage(error));
    } finally {
      setIsSessionLoading(false);
    }
  }, [currentSessionId, loadSessionEssentials, refreshSessions]);

  const toggleRagMode = useCallback(async () => {
    const next = !ragMode;
    setRagModeState(next);
    try {
      await setRagMode(next);
      setConnectionError(null);
    } catch (error) {
      setRagModeState(!next);
      setConnectionError(toErrorMessage(error));
    }
  }, [ragMode]);

  const toggleSkillRetrieval = useCallback(async () => {
    const next = !skillRetrievalEnabled;
    setSkillRetrievalEnabled(next);
    try {
      await setSkillRetrieval(next);
      setConnectionError(null);
    } catch (error) {
      setSkillRetrievalEnabled(!next);
      setConnectionError(toErrorMessage(error));
    }
  }, [skillRetrievalEnabled]);

  const updateExecutionPlatform = useCallback(
    async (platform: ExecutionPlatform) => {
      if (platform === executionPlatform) return;
      const previous = executionPlatform;
      setExecutionPlatformState(platform);
      try {
        await setExecutionPlatformRequest(platform);
        setConnectionError(null);
      } catch (error) {
        setExecutionPlatformState(previous);
        setConnectionError(toErrorMessage(error));
      }
    },
    [executionPlatform]
  );

  const rebuildKnowledgeIndex = useCallback(async () => {
    try {
      await rebuildKnowledgeIndexRequest();
      await refreshRuntime();
      setConnectionError(null);
    } catch (error) {
      setConnectionError(toErrorMessage(error));
    }
  }, [refreshRuntime]);

  const runStreamingSession = useCallback(
    async ({
      sessionId,
      initialAssistant,
      runner
    }: {
      sessionId: string;
      initialAssistant: Message;
      runner: (handlers: { onEvent: (event: string, data: Record<string, unknown>) => void }) => Promise<void>;
    }) => {
      let activeAssistantId = initialAssistant.id;
      let activeAssistantIndex = 0;
      let activeRunMeta = initialAssistant.runMeta;
      let pendingTokenBuffer = "";
      let tokenFlushHandle: number | null = null;

      setStreamingMessages([initialAssistant]);
      setIsStreaming(true);
      setConnectionError(null);

      const patchAssistant = (updater: (message: Message) => Message) => {
        setStreamingMessages((previous) =>
          updateMessageAtPosition(previous, activeAssistantIndex, activeAssistantId, updater)
        );
      };

      const flushTokenBuffer = () => {
        if (!pendingTokenBuffer) {
          tokenFlushHandle = null;
          return;
        }
        const nextChunk = pendingTokenBuffer;
        pendingTokenBuffer = "";
        tokenFlushHandle = null;
        startTransition(() => {
          patchAssistant((message) => ({ ...message, content: `${message.content}${nextChunk}` }));
        });
      };

      const scheduleTokenFlush = () => {
        if (tokenFlushHandle !== null) return;
        tokenFlushHandle = window.setTimeout(flushTokenBuffer, STREAM_TOKEN_FLUSH_MS);
      };

      const openNewAssistantIfNeeded = () => {
        setStreamingMessages((previous) => {
          const current = previous[activeAssistantIndex];
          if (current && !assistantHasPayload(current)) return previous;
          const nextAssistant = createAssistantDraft(activeRunMeta);
          activeAssistantId = nextAssistant.id;
          activeAssistantIndex = previous.length;
          return [...previous, nextAssistant];
        });
      };

      try {
        await runner({
          onEvent(event, data) {
            if (event === "title") {
              void refreshSessions();
              return;
            }
            if (event === "new_response") {
              flushTokenBuffer();
              openNewAssistantIfNeeded();
              return;
            }
            if (event === "token") {
              pendingTokenBuffer += String(data.content ?? "");
              scheduleTokenFlush();
              return;
            }
            if (event === "retrieval") {
              flushTokenBuffer();
              const step = normalizeRetrievalStep(data);
              if (!step) return;
              patchAssistant((message) => ({
                ...message,
                retrievalSteps: [...message.retrievalSteps, step]
              }));
              return;
            }
            if (event === "tool_start") {
              flushTokenBuffer();
              patchAssistant((message) => ({
                ...message,
                toolCalls: [
                  ...message.toolCalls,
                  { tool: String(data.tool ?? "tool"), input: String(data.input ?? ""), output: "" }
                ]
              }));
              return;
            }
            if (event === "tool_end") {
              flushTokenBuffer();
              patchAssistant((message) => ({
                ...message,
                toolCalls: message.toolCalls.map((toolCall, index, list) =>
                  index === list.length - 1
                    ? { ...toolCall, output: String(data.output ?? "") }
                    : toolCall
                )
              }));
              return;
            }
            if (event === "run_status") {
              const nextMeta = normalizeRunMeta(data);
              if (!nextMeta) return;
              activeRunMeta = nextMeta;
              patchAssistant((message) => ({ ...message, runMeta: nextMeta }));
              return;
            }
            if (event === "checkpoint_created" || event === "checkpoint_resumed" || event === "checkpoint_interrupted") {
              const checkpointEvent = normalizeCheckpointEvent({
                ...data,
                type: event === "checkpoint_created" ? "created" : event === "checkpoint_resumed" ? "resumed" : "interrupted"
              });
              if (!checkpointEvent) return;
              patchAssistant((message) => ({
                ...message,
                checkpointEvents: [...message.checkpointEvents, checkpointEvent],
                runMeta: {
                  ...(message.runMeta ?? activeRunMeta ?? {
                    status: "fresh" as RunStatus,
                    thread_id: "",
                    checkpoint_id: "",
                    resume_source: "",
                    orchestration_engine: "langgraph"
                  }),
                  status: event === "checkpoint_interrupted" ? "interrupted" : (message.runMeta?.status ?? activeRunMeta?.status ?? "fresh"),
                  checkpoint_id: checkpointEvent.checkpoint_id,
                  thread_id: checkpointEvent.thread_id || message.runMeta?.thread_id || activeRunMeta?.thread_id || "",
                  resume_source: checkpointEvent.resume_source || message.runMeta?.resume_source || activeRunMeta?.resume_source || "",
                  orchestration_engine: checkpointEvent.orchestration_engine || message.runMeta?.orchestration_engine || activeRunMeta?.orchestration_engine || "langgraph"
                }
              }));
              activeRunMeta = {
                ...(activeRunMeta ?? {
                  status: "fresh",
                  thread_id: "",
                  checkpoint_id: "",
                  resume_source: "",
                  orchestration_engine: "langgraph"
                }),
                status: event === "checkpoint_interrupted" ? "interrupted" : activeRunMeta?.status ?? "fresh",
                checkpoint_id: checkpointEvent.checkpoint_id,
                thread_id: checkpointEvent.thread_id || activeRunMeta?.thread_id || "",
                resume_source: checkpointEvent.resume_source || activeRunMeta?.resume_source || "",
                orchestration_engine: checkpointEvent.orchestration_engine || activeRunMeta?.orchestration_engine || "langgraph"
              };
              return;
            }
            if (event === "hitl_requested" || event === "hitl_approved" || event === "hitl_rejected" || event === "hitl_edited") {
              const hitlEvent = normalizeHitlEvent({
                ...data,
                type:
                  event === "hitl_requested"
                    ? "requested"
                    : event === "hitl_approved"
                      ? "approved"
                      : event === "hitl_edited"
                        ? "edited"
                        : "rejected"
              });
              if (!hitlEvent) return;
              patchAssistant((message) => ({
                ...message,
                hitlEvents: [...message.hitlEvents, hitlEvent],
                runMeta:
                  event === "hitl_requested"
                    ? {
                        ...(message.runMeta ?? activeRunMeta ?? {
                          status: "interrupted" as RunStatus,
                          thread_id: "",
                          checkpoint_id: "",
                          resume_source: "hitl_api",
                          orchestration_engine: "langgraph"
                        }),
                        status: "interrupted",
                        checkpoint_id: hitlEvent.checkpoint_id,
                        thread_id: hitlEvent.thread_id || message.runMeta?.thread_id || activeRunMeta?.thread_id || "",
                        resume_source: hitlEvent.resume_source || message.runMeta?.resume_source || activeRunMeta?.resume_source || "hitl_api",
                        orchestration_engine: hitlEvent.orchestration_engine || message.runMeta?.orchestration_engine || activeRunMeta?.orchestration_engine || "langgraph"
                      }
                    : message.runMeta
              }));
              if (event === "hitl_requested") setPendingHitl(buildPendingHitlFromEvent(hitlEvent));
              else setPendingHitl(null);
              return;
            }
            if (event === "done") {
              flushTokenBuffer();
              const finalContent = String(data.content ?? "");
              patchAssistant((message) => (message.content ? message : { ...message, content: finalContent }));
              const usage = normalizeUsage(data.usage);
              if (usage) patchAssistant((message) => ({ ...message, usage }));
              const runMeta = normalizeRunMeta(data.run_meta);
              if (runMeta) {
                activeRunMeta = runMeta;
                patchAssistant((message) => ({ ...message, runMeta }));
              }
              const checkpointEvents = Array.isArray(data.checkpoint_events)
                ? data.checkpoint_events.map((item) => normalizeCheckpointEvent(item)).filter((item): item is CheckpointEvent => item !== null)
                : [];
              if (checkpointEvents.length) patchAssistant((message) => ({ ...message, checkpointEvents }));
              const hitlEvents = Array.isArray(data.hitl_events)
                ? data.hitl_events.map((item) => normalizeHitlEvent(item)).filter((item): item is HitlEvent => item !== null)
                : [];
              if (hitlEvents.length) patchAssistant((message) => ({ ...message, hitlEvents }));
              return;
            }
            if (event === "error") {
              flushTokenBuffer();
              patchAssistant((message) => ({
                ...message,
                content: message.content || `Request failed: ${String(data.error ?? "unknown error")}`
              }));
            }
          }
        });
        setConnectionError(null);
      } catch (error) {
        if (tokenFlushHandle !== null) {
          window.clearTimeout(tokenFlushHandle);
          tokenFlushHandle = null;
        }
        flushTokenBuffer();
        const errorMessage = toErrorMessage(error);
        setConnectionError(errorMessage);
        patchAssistant((message) => ({
          ...message,
          content: message.content || `Request failed: ${errorMessage}`
        }));
      } finally {
        if (tokenFlushHandle !== null) window.clearTimeout(tokenFlushHandle);
        flushTokenBuffer();
        setIsStreaming(false);
        if (streamingMessagesRef.current.length) {
          setMessages((previous) => [...previous, ...streamingMessagesRef.current]);
          setStreamingMessages([]);
        }
        try {
          await Promise.all([refreshSessions(), refreshSessionTokens(sessionId), refreshCheckpoints(sessionId)]);
          setConnectionError(null);
        } catch (error) {
          setConnectionError(toErrorMessage(error));
        }
      }
    },
    [refreshCheckpoints, refreshSessionTokens, refreshSessions, setStreamingMessages]
  );

  const sendMessage = useCallback(
    async (value: string) => {
      const trimmedValue = value.trim();
      if (!trimmedValue || isStreaming || isSessionLoading) return;
      try {
        const sessionId = await ensureSession();
        setMessages((previous) => [
          ...previous,
          {
            id: makeId(),
            role: "user",
            content: trimmedValue,
            toolCalls: [],
            retrievalSteps: [],
            usage: null,
            runMeta: null,
            checkpointEvents: [],
            hitlEvents: []
          }
        ]);
        await runStreamingSession({
          sessionId,
          initialAssistant: createAssistantDraft({
            status: "fresh",
            thread_id: sessionId,
            checkpoint_id: "",
            resume_source: "",
            orchestration_engine: "langgraph"
          }),
          runner: (handlers) => streamChat({ message: trimmedValue, session_id: sessionId }, handlers)
        });
      } catch (error) {
        setConnectionError(toErrorMessage(error));
      }
    },
    [ensureSession, isSessionLoading, isStreaming, runStreamingSession]
  );

  const resumeCheckpoint = useCallback(
    async (checkpointId: string) => {
      if (!currentSessionId || !checkpointId || isStreaming || isSessionLoading) return;
      await runStreamingSession({
        sessionId: currentSessionId,
        initialAssistant: createAssistantDraft({
          status: "restoring",
          thread_id: currentSessionId,
          checkpoint_id: checkpointId,
          resume_source: "checkpoint_api",
          orchestration_engine: "langgraph"
        }),
        runner: (handlers) =>
          streamCheckpointResume({ session_id: currentSessionId, checkpoint_id: checkpointId }, handlers)
      });
    },
    [currentSessionId, isSessionLoading, isStreaming, runStreamingSession]
  );

  const submitHitlDecision = useCallback(
    async (checkpointId: string, decision: "approve" | "reject" | "edit", editedInput?: Record<string, unknown>) => {
      if (!currentSessionId || !checkpointId || isStreaming || isSessionLoading) return;
      await runStreamingSession({
        sessionId: currentSessionId,
        initialAssistant: createAssistantDraft({
          status: decision === "approve" ? "restoring" : decision === "edit" ? "edited" : "interrupted",
          thread_id: currentSessionId,
          checkpoint_id: checkpointId,
          resume_source: "hitl_api",
          orchestration_engine: "langgraph"
        }),
        runner: (handlers) =>
          streamHitlDecision(
            { session_id: currentSessionId, checkpoint_id: checkpointId, decision, edited_input: editedInput },
            handlers
          )
      });
    },
    [currentSessionId, isSessionLoading, isStreaming, runStreamingSession]
  );

  const sessionValue = useMemo<SessionStore>(
    () => ({
      sessions,
      currentSessionId,
      currentSessionTitle,
      createNewSession,
      selectSession,
      renameCurrentSession,
      removeSession,
      compressCurrentSession
    }),
    [compressCurrentSession, createNewSession, currentSessionId, currentSessionTitle, removeSession, renameCurrentSession, selectSession, sessions]
  );

  const chatValue = useMemo<ChatStore>(
    () => ({
      messages,
      streamingMessages: streamingMessagesState,
      checkpoints,
      pendingHitl,
      hitlAudit,
      mcpCapabilities,
      sessionContext,
      isInitializing,
      isSessionLoading,
      isStreaming,
      assetsLoading,
      connectionError,
      tokenStats,
      retryInitialization,
        sendMessage,
        resumeCheckpoint,
        submitHitlDecision,
        refreshCheckpoints: async () => refreshCheckpoints(),
        refreshAssets: async () => refreshCheckpoints(),
        triggerConsolidation
      }),
    [assetsLoading, checkpoints, connectionError, hitlAudit, isInitializing, isSessionLoading, isStreaming, mcpCapabilities, messages, pendingHitl, refreshCheckpoints, retryInitialization, resumeCheckpoint, sendMessage, sessionContext, streamingMessagesState, submitHitlDecision, tokenStats, triggerConsolidation]
  );

  const runtimeValue = useMemo<RuntimeStore>(
    () => ({
      ragMode,
      skillRetrievalEnabled,
      executionPlatform,
      knowledgeIndexStatus,
      runtimeReady,
      runtimeLoading,
      toggleRagMode,
      toggleSkillRetrieval,
      updateExecutionPlatform,
      rebuildKnowledgeIndex,
      refreshRuntime
    }),
    [executionPlatform, knowledgeIndexStatus, ragMode, rebuildKnowledgeIndex, refreshRuntime, runtimeLoading, runtimeReady, skillRetrievalEnabled, toggleRagMode, toggleSkillRetrieval, updateExecutionPlatform]
  );

  const inspectorValue = useMemo<InspectorStore>(
    () => ({
      skills,
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
    }),
    [editableFiles, ensureInspectorCatalog, inspectorCatalogLoading, inspectorCatalogReady, inspectorContent, inspectorDirty, inspectorFileLoading, inspectorPath, inspectorSaving, loadInspectorFile, saveInspector, skills, updateInspectorContent]
  );

  return (
    <SessionContext.Provider value={sessionValue}>
      <ChatContext.Provider value={chatValue}>
        <RuntimeContext.Provider value={runtimeValue}>
          <InspectorContext.Provider value={inspectorValue}>{children}</InspectorContext.Provider>
        </RuntimeContext.Provider>
      </ChatContext.Provider>
    </SessionContext.Provider>
  );
}

export function useSessionStore() {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSessionStore must be used inside AppProvider");
  return value;
}

export function useChatStore() {
  const value = useContext(ChatContext);
  if (!value) throw new Error("useChatStore must be used inside AppProvider");
  return value;
}

export function useRuntimeStore() {
  const value = useContext(RuntimeContext);
  if (!value) throw new Error("useRuntimeStore must be used inside AppProvider");
  return value;
}

export function useInspectorStore() {
  const value = useContext(InspectorContext);
  if (!value) throw new Error("useInspectorStore must be used inside AppProvider");
  return value;
}

export function useAppStore() {
  const session = useSessionStore();
  const chat = useChatStore();
  const runtime = useRuntimeStore();
  const inspector = useInspectorStore();
  return useMemo(() => ({ ...session, ...chat, ...runtime, ...inspector }), [chat, inspector, runtime, session]);
}
