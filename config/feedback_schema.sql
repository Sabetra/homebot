
-- Feedback-Tabellen für RAG-Chatbot
-- Zu implementieren in agent/rag_store.py

CREATE TABLE IF NOT EXISTS feedback_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    conversation_id TEXT,
    response_id TEXT,
    feedback_type TEXT,  -- 'thumbs_up', 'thumbs_down', 'rating'
    feedback_value INTEGER,  -- 1/-1 für thumbs, 1-5 für rating
    feedback_category TEXT,  -- 'quality', 'sources', 'completeness'
    comment TEXT,  -- Optional freitext
    context_metadata TEXT,  -- JSON mit RAG-Kontext
    created_at TEXT,
    ip_hash TEXT,  -- Anonymisiert für Missbrauchsschutz
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS source_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    doc_id TEXT,
    chunk_id TEXT,
    relevance_rating INTEGER,  -- 1-5
    helpful BOOLEAN,  -- True/False
    created_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Indizes für bessere Performance
CREATE INDEX IF NOT EXISTS idx_feedback_user_id ON feedback_responses(user_id);
CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback_responses(created_at);
CREATE INDEX IF NOT EXISTS idx_source_feedback_doc_id ON source_feedback(doc_id);
