VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

VERSION_SEED = """
INSERT OR IGNORE INTO version (id, schema_version) VALUES (1, 3);
"""

VERSION_UPDATE = """
UPDATE version SET schema_version = 3 WHERE id = 1 AND schema_version < 3;
"""

JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT,
    status TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    result_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT
);
"""

UPLOAD_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS upload_sessions (
    chat_id TEXT PRIMARY KEY,
    site TEXT,
    task TEXT,
    when_label TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

UPLOAD_SESSIONS_UPDATED_AT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS upload_sessions_updated_at
AFTER UPDATE ON upload_sessions
FOR EACH ROW
BEGIN
    UPDATE upload_sessions SET updated_at = CURRENT_TIMESTAMP WHERE chat_id = OLD.chat_id;
END;
"""

DOWNLOAD_QUEUE_TABLE = """
CREATE TABLE IF NOT EXISTS download_queue (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    file_unique_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    user_id TEXT,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    progress INTEGER NOT NULL DEFAULT 0,
    destination TEXT,
    site TEXT,
    task TEXT,
    when_label TEXT,
    message_id TEXT,
    message_date TEXT,
    caption TEXT,
    error TEXT,
    next_attempt_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT
);
"""

DOWNLOAD_QUEUE_FILE_UNIQUE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_download_queue_file_unique_id
ON download_queue(file_unique_id);
"""

DOWNLOAD_QUEUE_STATUS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_download_queue_status_created
ON download_queue(status, created_at);
"""

DOWNLOAD_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS download_history (
    id TEXT PRIMARY KEY,
    file_unique_id TEXT NOT NULL UNIQUE,
    file_id TEXT NOT NULL,
    destination TEXT,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

DOWNLOAD_QUEUE_UPDATED_AT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS download_queue_updated_at
AFTER UPDATE ON download_queue
FOR EACH ROW
BEGIN
    UPDATE download_queue SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;
"""

JOBS_UPDATED_AT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS jobs_updated_at
AFTER UPDATE ON jobs
FOR EACH ROW
BEGIN
    UPDATE jobs SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;
"""

SCHEMA = (
    VERSION_TABLE,
    VERSION_SEED,
    VERSION_UPDATE,
    JOBS_TABLE,
    JOBS_UPDATED_AT_TRIGGER,
    UPLOAD_SESSIONS_TABLE,
    UPLOAD_SESSIONS_UPDATED_AT_TRIGGER,
    DOWNLOAD_QUEUE_TABLE,
    DOWNLOAD_QUEUE_FILE_UNIQUE_INDEX,
    DOWNLOAD_QUEUE_STATUS_INDEX,
    DOWNLOAD_HISTORY_TABLE,
    DOWNLOAD_QUEUE_UPDATED_AT_TRIGGER,
)
