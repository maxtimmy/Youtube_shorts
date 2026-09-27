CREATE TABLE IF NOT EXISTS serials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_slug TEXT NOT NULL UNIQUE,
    serial_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_id INTEGER NOT NULL REFERENCES serials(id) ON DELETE CASCADE,
    episode_base_name TEXT NOT NULL,
    episode_number INTEGER,
    file_name TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(serial_id, episode_base_name)
);

CREATE TABLE IF NOT EXISTS shorts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    short_name TEXT NOT NULL UNIQUE,
    short_part INTEGER NOT NULL DEFAULT 1,
    duration REAL,
    final_path TEXT,
    clip_path TEXT,
    score REAL,
    text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIEW IF NOT EXISTS shorts_catalog AS
SELECT
    sh.id,
    s.serial_slug,
    s.serial_name,
    e.episode_base_name,
    e.episode_number,
    sh.short_name,
    sh.short_part,
    sh.duration,
    sh.final_path,
    sh.clip_path,
    sh.score,
    sh.text
FROM shorts sh
JOIN episodes e ON e.id = sh.episode_id
JOIN serials s ON s.id = e.serial_id;

CREATE INDEX IF NOT EXISTS idx_episodes_serial_id ON episodes(serial_id);
CREATE INDEX IF NOT EXISTS idx_shorts_episode_id ON shorts(episode_id);
