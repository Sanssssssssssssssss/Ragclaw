ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS compressed_context TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS excluded_turn_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS excluded_run_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS turn_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

ALTER TABLE session_messages
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_archived_at ON sessions(archived_at, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_messages_session_created_at ON session_messages(session_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS idx_session_messages_turn_id ON session_messages(turn_id);
CREATE INDEX IF NOT EXISTS idx_session_messages_run_id ON session_messages(run_id);
CREATE INDEX IF NOT EXISTS idx_session_messages_archived_at ON session_messages(archived_at, created_at ASC, id ASC);
