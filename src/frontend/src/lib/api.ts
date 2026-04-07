export type ToolCall = {
  tool: string;
  input: string;
  output: string;
};

export type RunStatus = "fresh" | "resumed" | "interrupted" | "restoring";

export type RunMeta = {
  status: RunStatus;
  thread_id: string;
  checkpoint_id: string;
  resume_source: string;
  orchestration_engine: string;
};

export type CheckpointEvent = {
  type: "created" | "resumed" | "interrupted";
  checkpoint_id: string;
  thread_id: string;
  resume_source: string;
  state_label: string;
  created_at: string;
  orchestration_engine: string;
};

export type HitlEvent = {
  type: "requested" | "approved" | "rejected";
  run_id: string;
  thread_id: string;
  session_id: string;
  capability_id: string;
  capability_type: string;
  display_name: string;
  risk_level: string;
  reason: string;
  proposed_input: Record<string, unknown>;
  checkpoint_id: string;
  resume_source: string;
  orchestration_engine: string;
};

export type PendingHitlInterrupt = {
  run_id: string;
  thread_id: string;
  session_id: string | null;
  capability_id: string;
  capability_type: string;
  display_name: string;
  risk_level: string;
  reason: string;
  proposed_input: Record<string, unknown>;
  checkpoint_id: string;
};

export type ExecutionPlatform = "windows" | "linux";

export type MessageUsage = {
  input_tokens: number;
  output_tokens: number;
};

export type Evidence = {
  source_path: string;
  source_type: string;
  locator: string;
  snippet: string;
  channel: "memory" | "skill" | "vector" | "bm25" | "fused";
  score: number | null;
  parent_id: string | null;
};

export type RetrievalStep = {
  kind: "memory" | "knowledge";
  stage: string;
  title: string;
  message: string;
  results: Evidence[];
};

export type KnowledgeIndexStatus = {
  ready: boolean;
  building: boolean;
  last_built_at: number | null;
  indexed_files: number;
  vector_ready: boolean;
  bm25_ready: boolean;
};

export type SessionSummary = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
};

export type SessionTokenStats = {
  system_tokens: number;
  message_tokens: number;
  total_tokens: number;
  session_trace_tokens: number;
  model_call_input_tokens: number;
  model_call_output_tokens: number;
  model_call_total_tokens: number;
};

export type CheckpointSummary = {
  checkpoint_id: string;
  thread_id: string;
  checkpoint_ns: string;
  created_at: string;
  source: string;
  step: number;
  run_id: string;
  session_id: string | null;
  user_message: string;
  route_intent: string;
  final_answer: string;
  is_latest: boolean;
  state_label: string;
  resume_eligible: boolean;
};

export type SessionHistory = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  compressed_context?: string;
  messages: Array<{
    role: "user" | "assistant";
    content: string;
    tool_calls?: ToolCall[];
    retrieval_steps?: RetrievalStep[];
    usage?: MessageUsage;
    run_meta?: RunMeta;
    checkpoint_events?: CheckpointEvent[];
    hitl_events?: HitlEvent[];
  }>;
};

export type StreamHandlers = {
  onEvent: (event: string, data: Record<string, unknown>) => void;
};

const DEFAULT_API_PORT = "8015";

export class ApiConnectionError extends Error {
  /**
   * Returns one connection-error object from base-url and detail string inputs and describes backend reachability failures.
   */
  constructor(
    public readonly baseUrl: string,
    public readonly detail: string
  ) {
    super(`Could not reach backend at ${baseUrl}. ${detail}`);
    this.name = "ApiConnectionError";
  }
}

/**
 * Returns one normalized API base string from a base URL input and strips a trailing slash when present.
 */
function normalizeApiBase(base: string) {
  return base.endsWith("/") ? base.slice(0, -1) : base;
}

/**
 * Returns one API base URL from environment or window inputs and resolves the frontend's backend origin.
 */
function getApiBase() {
  const configuredBase = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (configuredBase) {
    return normalizeApiBase(configuredBase);
  }

  if (typeof window === "undefined") {
    return `http://127.0.0.1:${DEFAULT_API_PORT}/api`;
  }

  return `${window.location.protocol}//${window.location.hostname}:${DEFAULT_API_PORT}/api`;
}

/**
 * Returns one connection-error object from base-url and unknown error inputs and normalizes network failures.
 */
function buildConnectionError(baseUrl: string, error: unknown) {
  if (error instanceof ApiConnectionError) {
    return error;
  }

  const detail =
    error instanceof Error && error.message.trim()
      ? error.message.trim()
      : "Make sure the backend is running, then retry.";
  return new ApiConnectionError(baseUrl, detail);
}

/**
 * Returns one parsed JSON response from path and fetch-init inputs and performs a typed API request.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const apiBase = getApiBase();
  let response: Response;

  try {
    response = await fetch(`${apiBase}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {})
      }
    });
  } catch (error) {
    throw buildConnectionError(apiBase, error);
  }

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }

  return (await response.json()) as T;
}

/**
 * Returns a session-summary list from no inputs and fetches all stored chat sessions.
 */
export async function listSessions() {
  return request<SessionSummary[]>("/sessions");
}

/**
 * Returns one created session summary from an optional title input and creates a new chat session.
 */
export async function createSession(title = "New Session") {
  return request<SessionSummary>("/sessions", {
    method: "POST",
    body: JSON.stringify({ title })
  });
}

/**
 * Returns one updated session summary from session id and title inputs and renames a stored session.
 */
export async function renameSession(sessionId: string, title: string) {
  return request<SessionSummary>(`/sessions/${sessionId}`, {
    method: "PUT",
    body: JSON.stringify({ title })
  });
}

/**
 * Returns one deletion result from a session id input and removes a stored session.
 */
export async function deleteSession(sessionId: string) {
  return request<{ ok: boolean }>(`/sessions/${sessionId}`, {
    method: "DELETE"
  });
}

/**
 * Returns one full session history from a session id input and loads historical chat messages.
 */
export async function getSessionHistory(sessionId: string) {
  return request<SessionHistory>(`/sessions/${sessionId}/history`);
}

export async function listSessionCheckpoints(sessionId: string) {
  return request<{ session_id: string; thread_id: string; checkpoints: CheckpointSummary[] }>(
    `/sessions/${sessionId}/checkpoints`
  );
}

export async function getSessionCheckpoint(sessionId: string, checkpointId: string) {
  return request<{ session_id: string; thread_id: string; checkpoint: CheckpointSummary }>(
    `/sessions/${sessionId}/checkpoints/${checkpointId}`
  );
}

export async function getPendingHitl(sessionId: string) {
  return request<{ session_id: string; thread_id: string; pending_interrupt: PendingHitlInterrupt | null }>(
    `/sessions/${sessionId}/hitl`
  );
}

/**
 * Returns one token summary from a session id input and fetches aggregate token counts for a session.
 */
export async function getSessionTokens(sessionId: string) {
  return request<SessionTokenStats>(`/tokens/session/${sessionId}`);
}

/**
 * Returns a skill summary list from no inputs and fetches editable skills metadata.
 */
export async function listSkills() {
  return request<Array<{ name: string; description: string; path: string }>>("/skills");
}

/**
 * Returns one file payload from a path input and loads a workspace file through the backend API.
 */
export async function loadFile(path: string) {
  return request<{ path: string; content: string }>(`/files?path=${encodeURIComponent(path)}`);
}

/**
 * Returns one save result from path and content inputs and persists a workspace file through the backend API.
 */
export async function saveFile(path: string, content: string) {
  return request<{ ok: boolean; path: string }>("/files", {
    method: "POST",
    body: JSON.stringify({ path, content })
  });
}

/**
 * Returns one rag-mode flag object from no inputs and fetches the current memory-retrieval toggle state.
 */
export async function getRagMode() {
  return request<{ enabled: boolean }>("/config/rag-mode");
}

/**
 * Returns one rag-mode flag object from a boolean input and updates the memory-retrieval toggle state.
 */
export async function setRagMode(enabled: boolean) {
  return request<{ enabled: boolean }>("/config/rag-mode", {
    method: "PUT",
    body: JSON.stringify({ enabled })
  });
}

/**
 * Returns one execution-platform object from no inputs and fetches the current shell-platform preference.
 */
export async function getExecutionPlatform() {
  return request<{ platform: ExecutionPlatform }>("/config/execution-platform");
}

/**
 * Returns one execution-platform object from a platform input and updates the current shell-platform preference.
 */
export async function setExecutionPlatform(platform: ExecutionPlatform) {
  return request<{ platform: ExecutionPlatform }>("/config/execution-platform", {
    method: "PUT",
    body: JSON.stringify({ platform })
  });
}

/**
 * Returns one skill-retrieval flag object from no inputs and fetches the current skill-first retrieval toggle state.
 */
export async function getSkillRetrieval() {
  return request<{ enabled: boolean }>("/config/skill-retrieval");
}

/**
 * Returns one skill-retrieval flag object from a boolean input and updates the current skill-first retrieval toggle state.
 */
export async function setSkillRetrieval(enabled: boolean) {
  return request<{ enabled: boolean }>("/config/skill-retrieval", {
    method: "PUT",
    body: JSON.stringify({ enabled })
  });
}

/**
 * Returns one compression summary from a session id input and archives older messages into compressed context.
 */
export async function compressSession(sessionId: string) {
  return request<{ archived_count: number; remaining_count: number }>(
    `/sessions/${sessionId}/compress`,
    { method: "POST" }
  );
}

/**
 * Returns one knowledge-index status object from no inputs and fetches current index readiness flags.
 */
export async function getKnowledgeIndexStatus() {
  return request<KnowledgeIndexStatus>("/knowledge/index/status");
}

/**
 * Returns one rebuild-acceptance result from no inputs and triggers a knowledge index rebuild.
 */
export async function rebuildKnowledgeIndex() {
  return request<{ accepted: boolean }>("/knowledge/index/rebuild", {
    method: "POST"
  });
}

/**
 * Returns no value from payload and handler inputs and streams SSE chat events to the frontend store.
 */
export async function streamChat(
  payload: {
    message: string;
    session_id: string;
  },
  handlers: StreamHandlers
) {
  const apiBase = getApiBase();
  let response: Response;

  try {
    response = await fetch(`${apiBase}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        ...payload,
        stream: true
      })
    });
  } catch (error) {
    throw buildConnectionError(apiBase, error);
  }

  if (!response.ok || !response.body) {
    throw new Error(`Chat request failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  /**
   * Returns no value from one SSE block string input and dispatches a parsed event to the caller's handler.
   */
  const flushBlock = (block: string) => {
    const lines = block.split("\n");
    let event = "message";
    const dataLines: string[] = [];

    for (const line of lines) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      }
      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
    }

    if (!dataLines.length) {
      return;
    }

    const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
    handlers.onEvent(event, data);
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });

    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      flushBlock(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }

    if (done) {
      if (buffer.trim()) {
        flushBlock(buffer);
      }
      break;
    }
  }
}

export async function streamCheckpointResume(
  payload: {
    session_id: string;
    checkpoint_id: string;
  },
  handlers: StreamHandlers
) {
  const apiBase = getApiBase();
  let response: Response;

  try {
    response = await fetch(
      `${apiBase}/sessions/${encodeURIComponent(payload.session_id)}/checkpoints/${encodeURIComponent(payload.checkpoint_id)}/resume`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ stream: true })
      }
    );
  } catch (error) {
    throw buildConnectionError(apiBase, error);
  }

  if (!response.ok || !response.body) {
    throw new Error(`Checkpoint resume failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushBlock = (block: string) => {
    const lines = block.split("\n");
    let event = "message";
    const dataLines: string[] = [];

    for (const line of lines) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      }
      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
    }

    if (!dataLines.length) {
      return;
    }

    const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
    handlers.onEvent(event, data);
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });

    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      flushBlock(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }

    if (done) {
      if (buffer.trim()) {
        flushBlock(buffer);
      }
      break;
    }
  }
}

export async function streamHitlDecision(
  payload: {
    session_id: string;
    checkpoint_id: string;
    decision: "approve" | "reject";
  },
  handlers: StreamHandlers
) {
  const apiBase = getApiBase();
  let response: Response;

  try {
    response = await fetch(
      `${apiBase}/sessions/${encodeURIComponent(payload.session_id)}/hitl/${encodeURIComponent(payload.checkpoint_id)}/decision`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ decision: payload.decision, stream: true })
      }
    );
  } catch (error) {
    throw buildConnectionError(apiBase, error);
  }

  if (!response.ok || !response.body) {
    throw new Error(`HITL decision failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushBlock = (block: string) => {
    const lines = block.split("\n");
    let event = "message";
    const dataLines: string[] = [];

    for (const line of lines) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      }
      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
    }

    if (!dataLines.length) {
      return;
    }

    const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
    handlers.onEvent(event, data);
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });

    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      flushBlock(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }

    if (done) {
      if (buffer.trim()) {
        flushBlock(buffer);
      }
      break;
    }
  }
}
