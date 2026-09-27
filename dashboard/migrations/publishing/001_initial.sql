CREATE TABLE IF NOT EXISTS youtube_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_slug TEXT NOT NULL UNIQUE,
    account_name TEXT NOT NULL,
    youtube_credential_name TEXT,
    youtube_credential_id TEXT UNIQUE,
    channel_id TEXT,
    channel_title TEXT,
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
    schedule_offset TEXT NOT NULL DEFAULT '+03:00',
    default_privacy_status TEXT NOT NULL DEFAULT 'private' CHECK(default_privacy_status IN ('private', 'unlisted', 'public')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS account_active_serials (
    account_id INTEGER PRIMARY KEY REFERENCES youtube_accounts(id) ON DELETE CASCADE,
    serial_slug TEXT NOT NULL,
    serial_name TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS account_schedule_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES youtube_accounts(id) ON DELETE CASCADE,
    slot_time TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, slot_time)
);

CREATE TABLE IF NOT EXISTS youtube_account_cooldowns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES youtube_accounts(id) ON DELETE CASCADE,
    reason_code TEXT NOT NULL,
    reason_message TEXT,
    source_workflow_name TEXT,
    source_node_name TEXT,
    blocked_at TEXT NOT NULL,
    blocked_until TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS youtube_publication_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES youtube_accounts(id) ON DELETE CASCADE,
    short_name TEXT NOT NULL,
    serial_slug TEXT,
    episode_base_name TEXT,
    youtube_title TEXT,
    youtube_video_id TEXT,
    youtube_url TEXT,
    privacy_status TEXT NOT NULL DEFAULT 'private',
    status TEXT NOT NULL DEFAULT 'scheduled',
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    publish_at_local TEXT,
    publish_at_utc TEXT,
    publish_date TEXT,
    publish_time TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, short_name)
);

CREATE INDEX IF NOT EXISTS idx_publication_account ON youtube_publication_history(account_id);
CREATE INDEX IF NOT EXISTS idx_publication_publish_at ON youtube_publication_history(publish_at_utc);
CREATE INDEX IF NOT EXISTS idx_cooldowns_account_active ON youtube_account_cooldowns(account_id, is_active);
