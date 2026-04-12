CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS session_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    turn_id TEXT,
    run_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(session_id, message_id)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT,
    thread_id TEXT,
    user_message TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'chat_api',
    started_at TEXT NOT NULL DEFAULT '',
    orchestration_engine TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL DEFAULT '',
    resume_source TEXT NOT NULL DEFAULT '',
    run_status TEXT NOT NULL DEFAULT 'fresh',
    status TEXT,
    final_answer TEXT NOT NULL DEFAULT '',
    route_intent TEXT NOT NULL DEFAULT '',
    used_skill TEXT NOT NULL DEFAULT '',
    tool_names JSONB NOT NULL DEFAULT '[]'::jsonb,
    retrieval_sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_message TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    jsonl_trace_path TEXT NOT NULL DEFAULT '',
    jsonl_summary_path TEXT NOT NULL DEFAULT '',
    event_count INTEGER NOT NULL DEFAULT 0,
    event_checksum TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_runs_session_started_at ON runs(session_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status_started_at ON runs(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_thread_started_at ON runs(thread_id, started_at DESC);

CREATE TABLE IF NOT EXISTS run_events (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    name TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(run_id, seq),
    UNIQUE(run_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_run_events_run_ts ON run_events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_run_events_name_ts ON run_events(name, ts DESC);

CREATE TABLE IF NOT EXISTS hitl_requests (
    request_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hitl_decisions (
    id BIGSERIAL PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES hitl_requests(request_id) ON DELETE CASCADE,
    checkpoint_id TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT '',
    actor_type TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS run_trace_parity (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    jsonl_event_count INTEGER NOT NULL DEFAULT 0,
    postgres_event_count INTEGER NOT NULL DEFAULT 0,
    jsonl_checksum TEXT NOT NULL DEFAULT '',
    postgres_checksum TEXT NOT NULL DEFAULT '',
    ordering_match BOOLEAN NOT NULL DEFAULT FALSE,
    mismatch_reason TEXT NOT NULL DEFAULT '',
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
