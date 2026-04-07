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
  type CheckpointEvent,
  type CheckpointSummary,
  type HitlEvent,
  type PendingHitlInterrupt,
  compressSession,
  createSession,
  deleteSession,
  getExecutionPlatform,
  getKnowledgeIndexStatus,
  getPendingHitl,
  getRagMode,
  getSkillRetrieval,
  getSessionHistory,
  listSessionCheckpoints,
  getSessionTokens,
  listSessions,
  listSkills,
  loadFile,
  renameSession,
  rebuildKnowledgeIndex as rebuildKnowledgeIndexRequest,
  type SessionTokenStats,
  saveFile,
  setExecutionPlatform as setExecutionPlatformRequest,
  setRagMode,
  setSkillRetrieval,
  streamChat,
  streamCheckpointResume,
  streamHitlDecision,
  type RunMeta,
  type RunStatus,
  type ExecutionPlatform,
  type Evidence,
  type KnowledgeIndexStatus,
  type MessageUsage,
  type RetrievalStep,
  type SessionSummary,
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

const STREAM_TOKEN_FLUSH_MS = 80;

type AppStore = {
  sessions: SessionSummary[];
  currentSessionId: string | null;
  checkpoints: CheckpointSummary[];
  pendingHitl: PendingHitlInterrupt | null;
  messages: Message[];
  streamingMessages: Message[];
  messageFeed: Message[];
  isInitializing: boolean;
  isStreaming: boolean;
  connectionError: string | null;
  ragMode: boolean;
  skillRetrievalEnabled: boolean;
  executionPlatform: ExecutionPlatform;
  skills: Array<{ name: string; description: string; path: string }>;
  editableFiles: string[];
  inspectorPath: string;
  inspectorContent: string;
  inspectorDirty: boolean;
  sidebarWidth: number;
  inspectorWidth: number;
  tokenStats: SessionTokenStats | null;
  knowledgeIndexStatus: KnowledgeIndexStatus | null;
  createNewSession: () => Promise<void>;
  retryInitialization: () => Promise<void>;
  selectSession: (sessionId: string) => Promise<void>;
  sendMessage: (value: string) => Promise<void>;
  resumeCheckpoint: (checkpointId: string) => Promise<void>;
  submitHitlDecision: (checkpointId: string, decision: "approve" | "reject") => Promise<void>;
  refreshCheckpoints: () => Promise<void>;
  toggleRagMode: () => Promise<void>;
  toggleSkillRetrieval: () => Promise<void>;
  updateExecutionPlatform: (platform: ExecutionPlatform) => Promise<void>;
  renameCurrentSession: (title: string) => Promise<void>;
  removeSession: (sessionId: string) => Promise<void>;
  loadInspectorFile: (path: string) => Promise<void>;
  updateInspectorContent: (value: string) => void;
  saveInspector: () => Promise<void>;
  compressCurrentSession: () => Promise<void>;
  rebuildKnowledgeIndex: () => Promise<void>;
  setSidebarWidth: (width: number) => void;
  setInspectorWidth: (width: number) => void;
};

const FIXED_FILES = [
  "workspace/SOUL.md",
  "workspace/IDENTITY.md",
  "workspace/USER.md",
  "workspace/AGENTS.md",
  "memory/MEMORY.md",
  "SKILLS_SNAPSHOT.md"
];

type SessionStore = Pick<
  AppStore,
  | "sessions"
  | "currentSessionId"
  | "createNewSession"
  | "selectSession"
  | "renameCurrentSession"
  | "removeSession"
  | "compressCurrentSession"
>;

type ChatStore = Pick<
  AppStore,
  | "messages"
  | "checkpoints"
  | "pendingHitl"
  | "streamingMessages"
  | "isInitializing"
  | "isStreaming"
  | "connectionError"
  | "tokenStats"
  | "retryInitialization"
  | "sendMessage"
  | "resumeCheckpoint"
  | "submitHitlDecision"
  | "refreshCheckpoints"
>;

type FeedStore = Pick<AppStore, "messageFeed">;

type RuntimeStore = Pick<
  AppStore,
  | "ragMode"
  | "toggleRagMode"
  | "skillRetrievalEnabled"
  | "toggleSkillRetrieval"
  | "executionPlatform"
  | "updateExecutionPlatform"
  | "knowledgeIndexStatus"
  | "rebuildKnowledgeIndex"
>;

type InspectorStore = Pick<
  AppStore,
  | "skills"
  | "editableFiles"
  | "inspectorPath"
  | "inspectorContent"
  | "inspectorDirty"
  | "loadInspectorFile"
  | "updateInspectorContent"
  | "saveInspector"
>;

type LayoutStore = Pick<
  AppStore,
  "sidebarWidth" | "inspectorWidth" | "setSidebarWidth" | "setInspectorWidth"
>;

const SessionContext = createContext<SessionStore | null>(null);
const ChatContext = createContext<ChatStore | null>(null);
const FeedContext = createContext<FeedStore | null>(null);
const RuntimeContext = createContext<RuntimeStore | null>(null);
const InspectorContext = createContext<InspectorStore | null>(null);
const LayoutContext = createContext<LayoutStore | null>(null);

/**
 * Returns one pseudo-random message id from no inputs and creates a temporary frontend identifier.
 */
function makeId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Returns one normalized evidence object from an unknown input and guards the frontend against malformed evidence payloads.
 */
function normalizeEvidence(value: unknown): Evidence | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const item = value as Record<string, unknown>;
  const scoreValue = item.score;
  const score =
    typeof scoreValue === "number"
      ? scoreValue
      : typeof scoreValue === "string" && scoreValue.trim()
        ? Number(scoreValue)
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

/**
 * Returns one normalized retrieval-step object from an unknown input and sanitizes streamed retrieval events for the UI.
 */
function normalizeRetrievalStep(value: unknown): RetrievalStep | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const item = value as Record<string, unknown>;
  const rawResults = Array.isArray(item.results) ? item.results : [];
  const results = rawResults
    .map((entry) => normalizeEvidence(entry))
    .filter((entry): entry is Evidence => entry !== null);

  return {
    kind: item.kind === "memory" ? "memory" : "knowledge",
    stage: String(item.stage ?? "unknown"),
    title: String(item.title ?? "Retrieval results"),
    message: String(item.message ?? ""),
    results
  };
}

/**
 * Returns one normalized token-usage object from an unknown input and validates per-message token accounting.
 */
function normalizeUsage(value: unknown): MessageUsage | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const item = value as Record<string, unknown>;
  const inputTokens = Number(item.input_tokens ?? 0);
  const outputTokens = Number(item.output_tokens ?? 0);

  if (!Number.isFinite(inputTokens) || !Number.isFinite(outputTokens)) {
    return null;
  }

  return {
    input_tokens: inputTokens,
    output_tokens: outputTokens
  };
}

function normalizeRunStatus(value: unknown): RunStatus {
  if (
    value === "fresh" ||
    value === "resumed" ||
    value === "interrupted" ||
    value === "restoring"
  ) {
    return value;
  }
  return "fresh";
}

function normalizeRunMeta(value: unknown): RunMeta | null {
  if (!value || typeof value !== "object") {
    return null;
  }

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
  if (!value || typeof value !== "object") {
    return null;
  }

  const item = value as Record<string, unknown>;
  const type = item.type;
  if (type !== "created" && type !== "resumed" && type !== "interrupted") {
    return null;
  }

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
  if (!value || typeof value !== "object") {
    return null;
  }

  const item = value as Record<string, unknown>;
  const type = item.type;
  if (type !== "requested" && type !== "approved" && type !== "rejected") {
    return null;
  }

  return {
    type,
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
    checkpoint_id: String(item.checkpoint_id ?? ""),
    resume_source: String(item.resume_source ?? ""),
    orchestration_engine: String(item.orchestration_engine ?? "")
  };
}

function normalizePendingHitl(value: unknown): PendingHitlInterrupt | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const item = value as Record<string, unknown>;
  return {
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
    checkpoint_id: String(item.checkpoint_id ?? "")
  };
}

/**
 * Returns one frontend message list from backend history input and converts API history into UI-friendly message objects.
 */
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

/**
 * Returns one display-safe error string from an unknown input and normalizes backend and network failures for the UI.
 */
function toErrorMessage(error: unknown) {
  if (error instanceof ApiConnectionError) {
    return `${error.message} If you started the app with backend/scripts/dev/start-dev.ps1, wait for the backend to finish booting and try again.`;
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }
  return "An unexpected error occurred while talking to the backend.";
}

/**
 * Returns one updated message list from previous messages, preferred index, and message id inputs.
 */
function updateMessageAtPosition(
  previous: Message[],
  preferredIndex: number,
  messageId: string,
  updater: (message: Message) => Message
) {
  const matchesPreferredIndex =
    preferredIndex >= 0 &&
    preferredIndex < previous.length &&
    previous[preferredIndex]?.id === messageId;

  const targetIndex = matchesPreferredIndex
    ? preferredIndex
    : previous.findIndex((message) => message.id === messageId);

  if (targetIndex === -1) {
    return previous;
  }

  const next = [...previous];
  next[targetIndex] = updater(previous[targetIndex]);
  return next;
}

/**
 * Returns a compact recent-message slice from full chat input for low-priority sidebar previews.
 */
function buildMessageFeed(messages: Message[]) {
  return messages
    .filter((message) => message.role === "user" || Boolean(message.content.trim()) || message.toolCalls.length > 0)
    .slice(-8);
}

/**
 * Returns one rendered provider tree from children input and owns the application state and side effects.
 */
export function AppProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [checkpoints, setCheckpoints] = useState<CheckpointSummary[]>([]);
  const [pendingHitl, setPendingHitl] = useState<PendingHitlInterrupt | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streamingMessagesState, setStreamingMessagesState] = useState<Message[]>([]);
  const [messageFeed, setMessageFeed] = useState<Message[]>([]);
  const [isInitializing, setIsInitializing] = useState(true);
  const [isStreaming, setIsStreaming] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [ragMode, setRagModeState] = useState(false);
  const [skillRetrievalEnabled, setSkillRetrievalEnabled] = useState(true);
  const [executionPlatform, setExecutionPlatformState] = useState<ExecutionPlatform>("windows");
  const [skills, setSkills] = useState<Array<{ name: string; description: string; path: string }>>(
    []
  );
  const [inspectorPath, setInspectorPath] = useState("memory/MEMORY.md");
  const [inspectorContent, setInspectorContent] = useState("");
  const [inspectorDirty, setInspectorDirty] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(272);
  const [inspectorWidth, setInspectorWidth] = useState(320);
  const [tokenStats, setTokenStats] = useState<SessionTokenStats | null>(null);
  const [knowledgeIndexStatus, setKnowledgeIndexStatus] = useState<KnowledgeIndexStatus | null>(
    null
  );
  const initializeAppRef = useRef<() => Promise<void>>(async () => {});
  const streamingMessagesRef = useRef<Message[]>([]);

  const editableFiles = useMemo(
    () => [...FIXED_FILES, ...skills.map((skill) => skill.path)],
    [skills]
  );

  /**
   * Returns no value from one next-state or updater input and keeps streaming assistant drafts in sync with a ref.
   */
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

  /**
   * Returns no value from no inputs and refreshes the session list from the backend.
   */
  const refreshSessions = useCallback(async function refreshSessions() {
    setSessions(await listSessions());
  }, []);

  const refreshCheckpoints = useCallback(async function refreshCheckpoints(sessionId?: string | null) {
    const resolvedSessionId = sessionId ?? currentSessionId;
    if (!resolvedSessionId) {
      setCheckpoints([]);
      setPendingHitl(null);
      return;
    }
    const [payload, pendingPayload] = await Promise.all([
      listSessionCheckpoints(resolvedSessionId),
      getPendingHitl(resolvedSessionId)
    ]);
    setCheckpoints(payload.checkpoints);
    setPendingHitl(normalizePendingHitl(pendingPayload.pending_interrupt));
  }, [currentSessionId]);

  /**
   * Returns no value from no inputs and refreshes editable skill metadata for the inspector.
   */
  const refreshSkills = useCallback(async function refreshSkills() {
    setSkills(await listSkills());
  }, []);

  /**
   * Returns no value from no inputs and refreshes knowledge-index readiness state.
   */
  const refreshKnowledgeIndexStatus = useCallback(async function refreshKnowledgeIndexStatus() {
    setKnowledgeIndexStatus(await getKnowledgeIndexStatus());
  }, []);

  /**
   * Returns no value from a session id input and refreshes both message history and aggregate token stats.
   */
  const refreshSessionDetails = useCallback(async function refreshSessionDetails(sessionId: string) {
    const [history, tokens, checkpointPayload, pendingPayload] = await Promise.all([
      getSessionHistory(sessionId),
      getSessionTokens(sessionId),
      listSessionCheckpoints(sessionId),
      getPendingHitl(sessionId)
    ]);
    const nextMessages = toUiMessages(history.messages);
    setMessages(nextMessages);
    setStreamingMessages([]);
    setMessageFeed(buildMessageFeed(nextMessages));
    setTokenStats(tokens);
    setCheckpoints(checkpointPayload.checkpoints);
    setPendingHitl(normalizePendingHitl(pendingPayload.pending_interrupt));
  }, [setStreamingMessages]);

  /**
   * Returns no value from a session id input and refreshes only aggregate token stats for the current chat.
   */
  const refreshSessionTokens = useCallback(async function refreshSessionTokens(sessionId: string) {
    setTokenStats(await getSessionTokens(sessionId));
  }, []);

  /**
   * Returns one action result or null from an async operation input and converts user-triggered backend failures into UI state.
   */
  const runUserAction = useCallback(async function runUserAction<T>(operation: () => Promise<T>) {
    try {
      const result = await operation();
      setConnectionError(null);
      return result;
    } catch (error) {
      setConnectionError(toErrorMessage(error));
      return null;
    }
  }, []);

  /**
   * Returns no value from no inputs and creates a fresh empty session in the store and backend.
   */
  const createNewSession = useCallback(async function createNewSession() {
    const created = await runUserAction(() => createSession());
    if (!created) {
      return;
    }

    await runUserAction(async () => {
      await refreshSessions();
      setCurrentSessionId(created.id);
      setMessages([]);
      setStreamingMessages([]);
      setMessageFeed([]);
      setCheckpoints([]);
      setPendingHitl(null);
      setTokenStats(null);
    });
  }, [refreshSessions, runUserAction, setStreamingMessages]);

  /**
   * Returns no value from a session id input and switches the UI to a different stored session.
   */
  const selectSession = useCallback(async function selectSession(sessionId: string) {
    const loaded = await runUserAction(async () => {
      await refreshSessionDetails(sessionId);
    });
    if (loaded === null) {
      return;
    }

    setCurrentSessionId(sessionId);
  }, [refreshSessionDetails, runUserAction]);

  /**
   * Returns one ensured session id from no inputs and creates a new session when no active session exists.
   */
  const ensureSession = useCallback(async function ensureSession() {
    if (currentSessionId) {
      return currentSessionId;
    }

    const created = await createSession();
    setCurrentSessionId(created.id);
    await refreshSessions();
    return created.id;
  }, [currentSessionId, refreshSessions]);

  /**
   * Returns no value from one prompt string input and streams a full user turn into the frontend store.
   */
  const sendMessage = useCallback(async function sendMessage(value: string) {
    if (!value.trim() || isStreaming) {
      return;
    }

    const userMessage: Message = {
      id: makeId(),
      role: "user",
      content: value.trim(),
      toolCalls: [],
      retrievalSteps: [],
      usage: null,
      runMeta: null,
      checkpointEvents: [],
      hitlEvents: []
    };
    const assistantMessage: Message = {
      id: makeId(),
      role: "assistant",
      content: "",
      toolCalls: [],
      retrievalSteps: [],
      usage: null,
      runMeta: {
        status: "fresh",
        thread_id: currentSessionId ?? "",
        checkpoint_id: "",
        resume_source: "",
        orchestration_engine: "langgraph"
      },
      checkpointEvents: [],
      hitlEvents: []
    };

    let activeAssistantIndex = -1;
    let activeRunMeta: RunMeta | null = assistantMessage.runMeta;

    setMessages((prev) => [...prev, userMessage]);
    setStreamingMessages(() => {
      activeAssistantIndex = 0;
      return [assistantMessage];
    });
    setIsStreaming(true);
    setConnectionError(null);

    let activeAssistantId = assistantMessage.id;
    let sessionId = currentSessionId;
    let pendingTokenBuffer = "";
    let tokenFlushHandle: number | null = null;

    /**
     * Returns no value from one message-updater callback input and patches the currently streaming assistant message.
     */
    const patchAssistant = (updater: (message: Message) => Message) => {
      setStreamingMessages((previous) =>
        updateMessageAtPosition(previous, activeAssistantIndex, activeAssistantId, updater)
      );
    };

    /**
     * Returns no value from no inputs and flushes buffered streamed tokens into the active assistant message.
     */
    const flushTokenBuffer = () => {
      if (!pendingTokenBuffer) {
        tokenFlushHandle = null;
        return;
      }

      const nextChunk = pendingTokenBuffer;
      pendingTokenBuffer = "";
      tokenFlushHandle = null;

      startTransition(() => {
        patchAssistant((message) => ({
          ...message,
          content: `${message.content}${nextChunk}`
        }));
      });
    };

    /**
     * Returns no value from no inputs and schedules the next buffered token flush on a short timer.
     */
    const scheduleTokenFlush = () => {
      if (tokenFlushHandle !== null) {
        return;
      }

      tokenFlushHandle = window.setTimeout(() => {
        flushTokenBuffer();
      }, STREAM_TOKEN_FLUSH_MS);
    };

    try {
      sessionId = await ensureSession();
      await streamChat(
        { message: value.trim(), session_id: sessionId },
        {
          onEvent(event, data) {
            if (event === "retrieval") {
              const step = normalizeRetrievalStep(data);
              if (!step) {
                return;
              }
              patchAssistant((message) => ({
                ...message,
                retrievalSteps: [...message.retrievalSteps, step]
              }));
              return;
            }

            if (event === "token") {
              pendingTokenBuffer += String(data.content ?? "");
              scheduleTokenFlush();
              return;
            }

            if (event === "tool_start") {
              flushTokenBuffer();
              patchAssistant((message) => ({
                ...message,
                toolCalls: [
                  ...message.toolCalls,
                  {
                    tool: String(data.tool ?? "tool"),
                    input: String(data.input ?? ""),
                    output: ""
                  }
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
              if (!nextMeta) {
                return;
              }
              activeRunMeta = nextMeta;
              patchAssistant((message) => ({
                ...message,
                runMeta: nextMeta
              }));
              return;
            }

              if (
                event === "checkpoint_created" ||
                event === "checkpoint_resumed" ||
                event === "checkpoint_interrupted"
            ) {
              const checkpointEvent = normalizeCheckpointEvent({
                ...data,
                type:
                  event === "checkpoint_created"
                    ? "created"
                    : event === "checkpoint_resumed"
                      ? "resumed"
                      : "interrupted"
              });
              if (!checkpointEvent) {
                return;
              }
              patchAssistant((message) => ({
                ...message,
                checkpointEvents: [...message.checkpointEvents, checkpointEvent],
                runMeta:
                  event === "checkpoint_interrupted"
                    ? {
                        ...(message.runMeta ?? {
                          status: "fresh" as RunStatus,
                          thread_id: "",
                          checkpoint_id: "",
                          resume_source: "",
                          orchestration_engine: "langgraph"
                        }),
                        status: "interrupted",
                        checkpoint_id: checkpointEvent.checkpoint_id,
                        thread_id: checkpointEvent.thread_id || message.runMeta?.thread_id || "",
                        resume_source: checkpointEvent.resume_source || message.runMeta?.resume_source || "",
                        orchestration_engine:
                          checkpointEvent.orchestration_engine || message.runMeta?.orchestration_engine || "langgraph"
                      }
                    : {
                        ...(message.runMeta ?? {
                          status: "fresh" as RunStatus,
                          thread_id: "",
                          checkpoint_id: "",
                          resume_source: "",
                          orchestration_engine: "langgraph"
                        }),
                        checkpoint_id: checkpointEvent.checkpoint_id,
                        thread_id: checkpointEvent.thread_id || message.runMeta?.thread_id || "",
                        resume_source: checkpointEvent.resume_source || message.runMeta?.resume_source || "",
                        orchestration_engine:
                          checkpointEvent.orchestration_engine || message.runMeta?.orchestration_engine || "langgraph"
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

              if (
                event === "hitl_requested" ||
                event === "hitl_approved" ||
                event === "hitl_rejected"
              ) {
                const hitlEvent = normalizeHitlEvent({
                  ...data,
                  type:
                    event === "hitl_requested"
                      ? "requested"
                      : event === "hitl_approved"
                        ? "approved"
                        : "rejected"
                });
                if (!hitlEvent) {
                  return;
                }
                patchAssistant((message) => ({
                  ...message,
                  hitlEvents: [...message.hitlEvents, hitlEvent],
                  runMeta:
                    event === "hitl_requested"
                      ? {
                          ...(message.runMeta ?? assistantMessage.runMeta!),
                          status: "interrupted",
                          checkpoint_id: hitlEvent.checkpoint_id,
                          thread_id: hitlEvent.thread_id || message.runMeta?.thread_id || currentSessionId || "",
                          resume_source: hitlEvent.resume_source || message.runMeta?.resume_source || "hitl_api",
                          orchestration_engine:
                            hitlEvent.orchestration_engine || message.runMeta?.orchestration_engine || "langgraph"
                        }
                      : message.runMeta
                }));
                return;
              }

            if (event === "new_response") {
              flushTokenBuffer();
              const nextAssistant: Message = {
                id: makeId(),
                role: "assistant",
                content: "",
                toolCalls: [],
                retrievalSteps: [],
                usage: null,
                runMeta: activeRunMeta,
                checkpointEvents: [],
                hitlEvents: []
              };
              activeAssistantId = nextAssistant.id;
              setStreamingMessages((previous) => {
                activeAssistantIndex = previous.length;
                return [...previous, nextAssistant];
              });
              return;
            }

            if (event === "done") {
              flushTokenBuffer();
              const finalContent = String(data.content ?? "");
              patchAssistant((message) =>
                message.content
                  ? message
                  : {
                      ...message,
                      content: finalContent
                    }
              );
              const usage = normalizeUsage(data.usage);
              if (usage) {
                patchAssistant((message) => ({
                  ...message,
                  usage
                }));
              }
              const runMeta = normalizeRunMeta(data.run_meta);
              if (runMeta) {
                activeRunMeta = runMeta;
                patchAssistant((message) => ({
                  ...message,
                  runMeta
                }));
              }
              const checkpointEvents = Array.isArray(data.checkpoint_events)
                ? data.checkpoint_events
                    .map((item) => normalizeCheckpointEvent(item))
                    .filter((item): item is CheckpointEvent => item !== null)
                : [];
              if (checkpointEvents.length) {
                patchAssistant((message) => ({
                  ...message,
                    checkpointEvents
                  }));
                }
                const hitlEvents = Array.isArray(data.hitl_events)
                  ? data.hitl_events
                      .map((item) => normalizeHitlEvent(item))
                      .filter((item): item is HitlEvent => item !== null)
                  : [];
                if (hitlEvents.length) {
                  patchAssistant((message) => ({
                    ...message,
                    hitlEvents
                  }));
                }
                return;
              }

            if (event === "title") {
              void refreshSessions();
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
        }
      );
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
      setIsStreaming(false);

      if (tokenFlushHandle !== null) {
        window.clearTimeout(tokenFlushHandle);
        tokenFlushHandle = null;
      }
      flushTokenBuffer();

      if (streamingMessagesRef.current.length) {
        const finalizedMessages = streamingMessagesRef.current;
        setMessages((previous) => [...previous, ...finalizedMessages]);
        setStreamingMessages([]);
      }

      if (!sessionId) {
        return;
      }

      try {
        await refreshSessions();
        await refreshSessionTokens(sessionId);
        await refreshCheckpoints(sessionId);
        setConnectionError(null);
      } catch (error) {
        setConnectionError(toErrorMessage(error));
      }
    }
  }, [currentSessionId, ensureSession, isStreaming, refreshCheckpoints, refreshSessionTokens, refreshSessions, setStreamingMessages]);

  const resumeCheckpoint = useCallback(async function resumeCheckpoint(checkpointId: string) {
    if (!currentSessionId || !checkpointId || isStreaming) {
      return;
    }

    const assistantMessage: Message = {
      id: makeId(),
      role: "assistant",
      content: "",
      toolCalls: [],
      retrievalSteps: [],
      usage: null,
      runMeta: {
        status: "restoring",
        thread_id: currentSessionId,
        checkpoint_id: checkpointId,
        resume_source: "checkpoint_api",
        orchestration_engine: "langgraph"
      },
      checkpointEvents: [],
      hitlEvents: []
    };

    let activeAssistantIndex = -1;
    let activeRunMeta: RunMeta | null = assistantMessage.runMeta;
    setStreamingMessages(() => {
      activeAssistantIndex = 0;
      return [assistantMessage];
    });
    setIsStreaming(true);
    setConnectionError(null);

    let activeAssistantId = assistantMessage.id;
    let pendingTokenBuffer = "";
    let tokenFlushHandle: number | null = null;

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
        patchAssistant((message) => ({
          ...message,
          content: `${message.content}${nextChunk}`
        }));
      });
    };

    const scheduleTokenFlush = () => {
      if (tokenFlushHandle !== null) {
        return;
      }

      tokenFlushHandle = window.setTimeout(() => {
        flushTokenBuffer();
      }, STREAM_TOKEN_FLUSH_MS);
    };

    try {
      await streamCheckpointResume(
        { session_id: currentSessionId, checkpoint_id: checkpointId },
        {
          onEvent(event, data) {
            if (event === "retrieval") {
              const step = normalizeRetrievalStep(data);
              if (!step) {
                return;
              }
              patchAssistant((message) => ({
                ...message,
                retrievalSteps: [...message.retrievalSteps, step]
              }));
              return;
            }

            if (event === "token") {
              pendingTokenBuffer += String(data.content ?? "");
              scheduleTokenFlush();
              return;
            }

            if (event === "tool_start") {
              flushTokenBuffer();
              patchAssistant((message) => ({
                ...message,
                toolCalls: [
                  ...message.toolCalls,
                  {
                    tool: String(data.tool ?? "tool"),
                    input: String(data.input ?? ""),
                    output: ""
                  }
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
              if (!nextMeta) {
                return;
              }
              activeRunMeta = nextMeta;
              patchAssistant((message) => ({
                ...message,
                runMeta: nextMeta
              }));
              return;
            }

              if (
                event === "checkpoint_created" ||
                event === "checkpoint_resumed" ||
                event === "checkpoint_interrupted"
            ) {
              const checkpointEvent = normalizeCheckpointEvent({
                ...data,
                type:
                  event === "checkpoint_created"
                    ? "created"
                    : event === "checkpoint_resumed"
                      ? "resumed"
                      : "interrupted"
              });
              if (!checkpointEvent) {
                return;
              }
              patchAssistant((message) => ({
                ...message,
                checkpointEvents: [...message.checkpointEvents, checkpointEvent],
                runMeta:
                  event === "checkpoint_interrupted"
                    ? {
                        ...(message.runMeta ?? assistantMessage.runMeta!),
                        status: "interrupted",
                        checkpoint_id: checkpointEvent.checkpoint_id,
                        thread_id: checkpointEvent.thread_id || message.runMeta?.thread_id || currentSessionId,
                        resume_source: checkpointEvent.resume_source || message.runMeta?.resume_source || "checkpoint_api",
                        orchestration_engine:
                          checkpointEvent.orchestration_engine || message.runMeta?.orchestration_engine || "langgraph"
                      }
                    : {
                        ...(message.runMeta ?? assistantMessage.runMeta!),
                        checkpoint_id: checkpointEvent.checkpoint_id,
                        thread_id: checkpointEvent.thread_id || message.runMeta?.thread_id || currentSessionId,
                        resume_source: checkpointEvent.resume_source || message.runMeta?.resume_source || "checkpoint_api",
                        orchestration_engine:
                          checkpointEvent.orchestration_engine || message.runMeta?.orchestration_engine || "langgraph"
                      }
              }));
              activeRunMeta = {
                ...(activeRunMeta ?? assistantMessage.runMeta!),
                status: event === "checkpoint_interrupted" ? "interrupted" : activeRunMeta?.status ?? "restoring",
                checkpoint_id: checkpointEvent.checkpoint_id,
                thread_id: checkpointEvent.thread_id || activeRunMeta?.thread_id || currentSessionId,
                resume_source: checkpointEvent.resume_source || activeRunMeta?.resume_source || "checkpoint_api",
                orchestration_engine:
                  checkpointEvent.orchestration_engine || activeRunMeta?.orchestration_engine || "langgraph"
                };
                return;
              }

              if (
                event === "hitl_requested" ||
                event === "hitl_approved" ||
                event === "hitl_rejected"
              ) {
                const hitlEvent = normalizeHitlEvent({
                  ...data,
                  type:
                    event === "hitl_requested"
                      ? "requested"
                      : event === "hitl_approved"
                        ? "approved"
                        : "rejected"
                });
                if (!hitlEvent) {
                  return;
                }
                patchAssistant((message) => ({
                  ...message,
                  hitlEvents: [...message.hitlEvents, hitlEvent],
                  runMeta:
                    event === "hitl_requested"
                      ? {
                          ...(message.runMeta ?? assistantMessage.runMeta!),
                          status: "interrupted",
                          checkpoint_id: hitlEvent.checkpoint_id,
                          thread_id: hitlEvent.thread_id || message.runMeta?.thread_id || currentSessionId || "",
                          resume_source: hitlEvent.resume_source || message.runMeta?.resume_source || "hitl_api",
                          orchestration_engine:
                            hitlEvent.orchestration_engine || message.runMeta?.orchestration_engine || "langgraph"
                        }
                      : message.runMeta
                }));
                return;
              }

            if (event === "new_response") {
              flushTokenBuffer();
              const nextAssistant: Message = {
                id: makeId(),
                role: "assistant",
                content: "",
                toolCalls: [],
                retrievalSteps: [],
                usage: null,
                runMeta: activeRunMeta,
                checkpointEvents: [],
                hitlEvents: []
              };
              activeAssistantId = nextAssistant.id;
              setStreamingMessages((previous) => {
                activeAssistantIndex = previous.length;
                return [...previous, nextAssistant];
              });
              return;
            }

            if (event === "done") {
              flushTokenBuffer();
              const finalContent = String(data.content ?? "");
              patchAssistant((message) =>
                message.content
                  ? message
                  : {
                      ...message,
                      content: finalContent
                    }
              );
              const usage = normalizeUsage(data.usage);
              if (usage) {
                patchAssistant((message) => ({
                  ...message,
                  usage
                }));
              }
              const runMeta = normalizeRunMeta(data.run_meta);
              if (runMeta) {
                activeRunMeta = runMeta;
                patchAssistant((message) => ({
                  ...message,
                  runMeta
                }));
              }
              const checkpointEvents = Array.isArray(data.checkpoint_events)
                ? data.checkpoint_events
                    .map((item) => normalizeCheckpointEvent(item))
                    .filter((item): item is CheckpointEvent => item !== null)
                : [];
              if (checkpointEvents.length) {
                patchAssistant((message) => ({
                  ...message,
                    checkpointEvents
                  }));
                }
                const hitlEvents = Array.isArray(data.hitl_events)
                  ? data.hitl_events
                      .map((item) => normalizeHitlEvent(item))
                      .filter((item): item is HitlEvent => item !== null)
                  : [];
                if (hitlEvents.length) {
                  patchAssistant((message) => ({
                    ...message,
                    hitlEvents
                  }));
                }
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
        }
      );
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
      setIsStreaming(false);

      if (tokenFlushHandle !== null) {
        window.clearTimeout(tokenFlushHandle);
        tokenFlushHandle = null;
      }
      flushTokenBuffer();

      if (streamingMessagesRef.current.length) {
        const finalizedMessages = streamingMessagesRef.current;
        setMessages((previous) => [...previous, ...finalizedMessages]);
        setStreamingMessages([]);
      }

      try {
        await refreshSessions();
        await refreshSessionDetails(currentSessionId);
        setConnectionError(null);
      } catch (error) {
        setConnectionError(toErrorMessage(error));
      }
    }
  }, [currentSessionId, isStreaming, refreshSessionDetails, refreshSessions, setStreamingMessages]);

  const submitHitlDecision = useCallback(async function submitHitlDecision(
    checkpointId: string,
    decision: "approve" | "reject"
  ) {
    if (!currentSessionId || !checkpointId || isStreaming) {
      return;
    }

    const assistantMessage: Message = {
      id: makeId(),
      role: "assistant",
      content: "",
      toolCalls: [],
      retrievalSteps: [],
      usage: null,
      runMeta: {
        status: "restoring",
        thread_id: currentSessionId,
        checkpoint_id: checkpointId,
        resume_source: "hitl_api",
        orchestration_engine: "langgraph"
      },
      checkpointEvents: [],
      hitlEvents: []
    };

    let activeAssistantIndex = -1;
    let activeRunMeta: RunMeta | null = assistantMessage.runMeta;
    setStreamingMessages(() => {
      activeAssistantIndex = 0;
      return [assistantMessage];
    });
    setIsStreaming(true);
    setConnectionError(null);

    let activeAssistantId = assistantMessage.id;

    const patchAssistant = (updater: (message: Message) => Message) => {
      setStreamingMessages((previous) =>
        updateMessageAtPosition(previous, activeAssistantIndex, activeAssistantId, updater)
      );
    };

    try {
      await streamHitlDecision(
        {
          session_id: currentSessionId,
          checkpoint_id: checkpointId,
          decision
        },
        {
          onEvent(event, data) {
            if (event === "run_status") {
              const nextMeta = normalizeRunMeta(data);
              if (!nextMeta) {
                return;
              }
              activeRunMeta = nextMeta;
              patchAssistant((message) => ({
                ...message,
                runMeta: nextMeta
              }));
              return;
            }

            if (
              event === "checkpoint_created" ||
              event === "checkpoint_resumed" ||
              event === "checkpoint_interrupted"
            ) {
              const checkpointEvent = normalizeCheckpointEvent({
                ...data,
                type:
                  event === "checkpoint_created"
                    ? "created"
                    : event === "checkpoint_resumed"
                      ? "resumed"
                      : "interrupted"
              });
              if (!checkpointEvent) {
                return;
              }
              patchAssistant((message) => ({
                ...message,
                checkpointEvents: [...message.checkpointEvents, checkpointEvent]
              }));
              return;
            }

            if (
              event === "hitl_requested" ||
              event === "hitl_approved" ||
              event === "hitl_rejected"
            ) {
              const hitlEvent = normalizeHitlEvent({
                ...data,
                type:
                  event === "hitl_requested"
                    ? "requested"
                    : event === "hitl_approved"
                      ? "approved"
                      : "rejected"
              });
              if (!hitlEvent) {
                return;
              }
              patchAssistant((message) => ({
                ...message,
                hitlEvents: [...message.hitlEvents, hitlEvent]
              }));
              return;
            }

            if (event === "new_response") {
              const nextAssistant: Message = {
                id: makeId(),
                role: "assistant",
                content: "",
                toolCalls: [],
                retrievalSteps: [],
                usage: null,
                runMeta: activeRunMeta,
                checkpointEvents: [],
                hitlEvents: []
              };
              activeAssistantId = nextAssistant.id;
              setStreamingMessages((previous) => {
                activeAssistantIndex = previous.length;
                return [...previous, nextAssistant];
              });
              return;
            }

            if (event === "token") {
              patchAssistant((message) => ({
                ...message,
                content: `${message.content}${String(data.content ?? "")}`
              }));
              return;
            }

            if (event === "done") {
              const finalContent = String(data.content ?? "");
              patchAssistant((message) =>
                message.content
                  ? message
                  : {
                      ...message,
                      content: finalContent
                    }
              );
              const usage = normalizeUsage(data.usage);
              if (usage) {
                patchAssistant((message) => ({
                  ...message,
                  usage
                }));
              }
              const runMeta = normalizeRunMeta(data.run_meta);
              if (runMeta) {
                activeRunMeta = runMeta;
                patchAssistant((message) => ({
                  ...message,
                  runMeta
                }));
              }
              const checkpointEvents = Array.isArray(data.checkpoint_events)
                ? data.checkpoint_events
                    .map((item) => normalizeCheckpointEvent(item))
                    .filter((item): item is CheckpointEvent => item !== null)
                : [];
              if (checkpointEvents.length) {
                patchAssistant((message) => ({
                  ...message,
                  checkpointEvents
                }));
              }
              const hitlEvents = Array.isArray(data.hitl_events)
                ? data.hitl_events
                    .map((item) => normalizeHitlEvent(item))
                    .filter((item): item is HitlEvent => item !== null)
                : [];
              if (hitlEvents.length) {
                patchAssistant((message) => ({
                  ...message,
                  hitlEvents
                }));
              }
              return;
            }

            if (event === "error") {
              patchAssistant((message) => ({
                ...message,
                content: message.content || `Request failed: ${String(data.error ?? "unknown error")}`
              }));
            }
          }
        }
      );
      setConnectionError(null);
    } catch (error) {
      const errorMessage = toErrorMessage(error);
      setConnectionError(errorMessage);
      patchAssistant((message) => ({
        ...message,
        content: message.content || `Request failed: ${errorMessage}`
      }));
    } finally {
      setIsStreaming(false);
      if (streamingMessagesRef.current.length) {
        const finalizedMessages = streamingMessagesRef.current;
        setMessages((previous) => [...previous, ...finalizedMessages]);
        setStreamingMessages([]);
      }

      try {
        await refreshSessions();
        await refreshSessionDetails(currentSessionId);
        setConnectionError(null);
      } catch (error) {
        setConnectionError(toErrorMessage(error));
      }
    }
  }, [currentSessionId, isStreaming, refreshSessionDetails, refreshSessions, setStreamingMessages]);

  /**
   * Returns no value from no inputs and flips the memory-retrieval mode while preserving rollback on failure.
   */
  const toggleRagMode = useCallback(async function toggleRagMode() {
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

  /**
   * Returns no value from no inputs and flips the skill-first retrieval toggle while preserving rollback on failure.
   */
  const toggleSkillRetrieval = useCallback(async function toggleSkillRetrieval() {
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

  /**
   * Returns no value from one execution-platform input and updates the shell-platform preference with rollback on failure.
   */
  const updateExecutionPlatform = useCallback(async function updateExecutionPlatform(
    platform: ExecutionPlatform
  ) {
    if (platform === executionPlatform) {
      return;
    }

    const previous = executionPlatform;
    setExecutionPlatformState(platform);
    try {
      await setExecutionPlatformRequest(platform);
      setConnectionError(null);
    } catch (error) {
      setExecutionPlatformState(previous);
      setConnectionError(toErrorMessage(error));
    }
  }, [executionPlatform]);

  /**
   * Returns no value from a title string input and renames the active session when a non-empty title is provided.
   */
  const renameCurrentSession = useCallback(async function renameCurrentSession(title: string) {
    if (!currentSessionId || !title.trim()) {
      return;
    }
    await runUserAction(async () => {
      await renameSession(currentSessionId, title.trim());
      await refreshSessions();
    });
  }, [currentSessionId, refreshSessions, runUserAction]);

  /**
   * Returns no value from a session id input and deletes the selected session while keeping the UI state consistent.
   */
  const removeSession = useCallback(async function removeSession(sessionId: string) {
    await runUserAction(async () => {
      await deleteSession(sessionId);
      await refreshSessions();
      if (currentSessionId === sessionId) {
        const nextSessions = await listSessions();
        setSessions(nextSessions);
        if (nextSessions.length) {
          setCurrentSessionId(nextSessions[0].id);
          await refreshSessionDetails(nextSessions[0].id);
        } else {
          setCurrentSessionId(null);
          setCheckpoints([]);
          setPendingHitl(null);
          setMessages([]);
          setStreamingMessages([]);
          setMessageFeed([]);
          setTokenStats(null);
        }
      }
    });
  }, [currentSessionId, refreshSessionDetails, refreshSessions, runUserAction, setStreamingMessages]);

  /**
   * Returns no value from a file-path input and loads one workspace file into the inspector.
   */
  const loadInspectorFile = useCallback(async function loadInspectorFile(path: string) {
    const file = await runUserAction(() => loadFile(path));
    if (!file) {
      return;
    }

    setInspectorPath(path);
    setInspectorContent(file.content);
    setInspectorDirty(false);
  }, [runUserAction]);

  /**
   * Returns no value from one content string input and updates the in-memory inspector buffer.
   */
  const updateInspectorContent = useCallback(function updateInspectorContent(value: string) {
    setInspectorContent(value);
    setInspectorDirty(true);
  }, []);

  /**
   * Returns no value from no inputs and persists the current inspector buffer through the backend API.
   */
  const saveInspector = useCallback(async function saveInspector() {
    const saved = await runUserAction(() => saveFile(inspectorPath, inspectorContent));
    if (!saved) {
      return;
    }

    setInspectorDirty(false);
    await runUserAction(async () => {
      await refreshSkills();
    });
  }, [inspectorContent, inspectorPath, refreshSkills, runUserAction]);

  /**
   * Returns no value from no inputs and compresses older messages for the active session.
   */
  const compressCurrentSession = useCallback(async function compressCurrentSession() {
    if (!currentSessionId) {
      return;
    }
    await runUserAction(async () => {
      await compressSession(currentSessionId);
      await refreshSessionDetails(currentSessionId);
      await refreshSessions();
    });
  }, [currentSessionId, refreshSessionDetails, refreshSessions, runUserAction]);

  /**
   * Returns no value from no inputs and triggers a knowledge-index rebuild followed by a status refresh.
   */
  const rebuildKnowledgeIndex = useCallback(async function rebuildKnowledgeIndex() {
    await runUserAction(async () => {
      await rebuildKnowledgeIndexRequest();
      await refreshKnowledgeIndexStatus();
    });
  }, [refreshKnowledgeIndexStatus, runUserAction]);

  /**
   * Returns no value from no inputs and hydrates the frontend store while degrading cleanly when the backend is unavailable.
   */
  const initializeApp = useCallback(async function initializeApp() {
    setIsInitializing(true);
    setConnectionError(null);

    try {
      const [skillRetrieval, initialSessions, rag, platform, initialSkills, initialKnowledgeIndexStatus] = await Promise.all([
        getSkillRetrieval(),
        listSessions(),
        getRagMode(),
        getExecutionPlatform(),
        listSkills(),
        getKnowledgeIndexStatus()
      ]);

      setSkillRetrievalEnabled(skillRetrieval.enabled);
      setSessions(initialSessions);
      setRagModeState(rag.enabled);
      setExecutionPlatformState(platform.platform);
      setSkills(initialSkills);
      setKnowledgeIndexStatus(initialKnowledgeIndexStatus);

      if (initialSessions.length) {
        setCurrentSessionId(initialSessions[0].id);
        await refreshSessionDetails(initialSessions[0].id);
      } else {
        const created = await createSession();
        setCurrentSessionId(created.id);
        setSessions([created]);
        setCheckpoints([]);
        setPendingHitl(null);
      }

      const file = await loadFile("memory/MEMORY.md");
      setInspectorPath(file.path);
      setInspectorContent(file.content);
      setInspectorDirty(false);
      setConnectionError(null);
    } catch (error) {
      setConnectionError(toErrorMessage(error));
      setSessions([]);
      setCurrentSessionId(null);
      setCheckpoints([]);
      setPendingHitl(null);
      setMessages([]);
      setStreamingMessages([]);
      setMessageFeed([]);
      setSkills([]);
      setTokenStats(null);
      setKnowledgeIndexStatus(null);
      setSkillRetrievalEnabled(true);
      setExecutionPlatformState("windows");
      setInspectorContent("");
      setInspectorDirty(false);
    } finally {
      setIsInitializing(false);
    }
  }, [refreshSessionDetails, setStreamingMessages]);

  /**
   * Returns no value from no inputs and retries the initial backend data load after a startup failure.
   */
  const retryInitialization = useCallback(async function retryInitialization() {
    await initializeApp();
  }, [initializeApp]);

  initializeAppRef.current = initializeApp;

  useEffect(() => {
    void initializeAppRef.current();
  }, []);

  useEffect(() => {
    if (!knowledgeIndexStatus?.building) {
      return;
    }

    const timer = window.setInterval(() => {
      void getKnowledgeIndexStatus().then((status) => setKnowledgeIndexStatus(status));
    }, 3000);

    return () => window.clearInterval(timer);
  }, [knowledgeIndexStatus?.building]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      startTransition(() => {
        setMessageFeed((previous) => {
          const next = buildMessageFeed([...messages, ...streamingMessagesState]);
          if (previous.length === next.length && previous.every((message, index) => message === next[index])) {
            return previous;
          }
          return next;
        });
      });
    }, isStreaming ? 260 : 120);

    return () => window.clearTimeout(timer);
  }, [messages, streamingMessagesState, isStreaming]);

  const sessionValue = useMemo<SessionStore>(
    () => ({
      sessions,
      currentSessionId,
      createNewSession,
      selectSession,
      renameCurrentSession,
      removeSession,
      compressCurrentSession
    }),
    [
      sessions,
      currentSessionId,
      createNewSession,
      selectSession,
      renameCurrentSession,
      removeSession,
      compressCurrentSession
    ]
  );

  const chatValue = useMemo<ChatStore>(
    () => ({
      messages,
      checkpoints,
      pendingHitl,
      streamingMessages: streamingMessagesState,
      isInitializing,
      isStreaming,
      connectionError,
      tokenStats,
      retryInitialization,
      sendMessage,
      resumeCheckpoint,
      submitHitlDecision,
      refreshCheckpoints
    }),
    [
      messages,
      checkpoints,
      pendingHitl,
      streamingMessagesState,
      isInitializing,
      isStreaming,
      connectionError,
      tokenStats,
      retryInitialization,
      sendMessage,
      resumeCheckpoint,
      submitHitlDecision,
      refreshCheckpoints
    ]
  );

  const feedValue = useMemo<FeedStore>(
    () => ({
      messageFeed
    }),
    [messageFeed]
  );

  const runtimeValue = useMemo<RuntimeStore>(
    () => ({
      ragMode,
      toggleRagMode,
      skillRetrievalEnabled,
      toggleSkillRetrieval,
      executionPlatform,
      updateExecutionPlatform,
      knowledgeIndexStatus,
      rebuildKnowledgeIndex
    }),
    [
      ragMode,
      toggleRagMode,
      skillRetrievalEnabled,
      toggleSkillRetrieval,
      executionPlatform,
      updateExecutionPlatform,
      knowledgeIndexStatus,
      rebuildKnowledgeIndex
    ]
  );

  const inspectorValue = useMemo<InspectorStore>(
    () => ({
      skills,
      editableFiles,
      inspectorPath,
      inspectorContent,
      inspectorDirty,
      loadInspectorFile,
      updateInspectorContent,
      saveInspector
    }),
    [
      skills,
      editableFiles,
      inspectorPath,
      inspectorContent,
      inspectorDirty,
      loadInspectorFile,
      updateInspectorContent,
      saveInspector
    ]
  );

  const layoutValue = useMemo<LayoutStore>(
    () => ({
      sidebarWidth,
      inspectorWidth,
      setSidebarWidth,
      setInspectorWidth
    }),
    [sidebarWidth, inspectorWidth]
  );

  return (
    <SessionContext.Provider value={sessionValue}>
      <FeedContext.Provider value={feedValue}>
        <ChatContext.Provider value={chatValue}>
          <RuntimeContext.Provider value={runtimeValue}>
            <InspectorContext.Provider value={inspectorValue}>
              <LayoutContext.Provider value={layoutValue}>{children}</LayoutContext.Provider>
            </InspectorContext.Provider>
          </RuntimeContext.Provider>
        </ChatContext.Provider>
      </FeedContext.Provider>
    </SessionContext.Provider>
  );
}

/**
 * Returns one app-store object from no explicit inputs and exposes the shared frontend state context to consumers.
 */
export function useAppStore() {
  const session = useSessionStore();
  const feed = useFeedStore();
  const chat = useChatStore();
  const runtime = useRuntimeStore();
  const inspector = useInspectorStore();
  const layout = useLayoutStore();

  return useMemo(
    () => ({
      ...session,
      ...feed,
      ...chat,
      ...runtime,
      ...inspector,
      ...layout
    }),
    [session, feed, chat, runtime, inspector, layout]
  );
}

/**
 * Returns one session-focused store object from no explicit inputs and exposes session list plus session actions.
 */
export function useSessionStore() {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error("useSessionStore must be used inside AppProvider");
  }
  return value;
}

/**
 * Returns one chat-focused store object from no explicit inputs and exposes streaming chat state and actions.
 */
export function useChatStore() {
  const value = useContext(ChatContext);
  if (!value) {
    throw new Error("useChatStore must be used inside AppProvider");
  }
  return value;
}

/**
 * Returns one feed-focused store object from no explicit inputs and exposes throttled sidebar previews.
 */
export function useFeedStore() {
  const value = useContext(FeedContext);
  if (!value) {
    throw new Error("useFeedStore must be used inside AppProvider");
  }
  return value;
}

/**
 * Returns one runtime-focused store object from no explicit inputs and exposes RAG and index controls.
 */
export function useRuntimeStore() {
  const value = useContext(RuntimeContext);
  if (!value) {
    throw new Error("useRuntimeStore must be used inside AppProvider");
  }
  return value;
}

/**
 * Returns one inspector-focused store object from no explicit inputs and exposes editor state and file actions.
 */
export function useInspectorStore() {
  const value = useContext(InspectorContext);
  if (!value) {
    throw new Error("useInspectorStore must be used inside AppProvider");
  }
  return value;
}

/**
 * Returns one layout-focused store object from no explicit inputs and exposes panel sizing state.
 */
export function useLayoutStore() {
  const value = useContext(LayoutContext);
  if (!value) {
    throw new Error("useLayoutStore must be used inside AppProvider");
  }
  return value;
}
