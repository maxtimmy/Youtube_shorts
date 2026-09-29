CREATE TABLE IF NOT EXISTS content_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    author TEXT,
    platform TEXT,
    license TEXT,
    checked_at TEXT,
    allowed_uses_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS short_sources (
    short_id INTEGER NOT NULL REFERENCES shorts(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES content_sources(id) ON DELETE CASCADE,
    use_type TEXT NOT NULL DEFAULT 'reference',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (short_id, source_id)
);

CREATE TABLE IF NOT EXISTS creative_records (
    short_id INTEGER PRIMARY KEY REFERENCES shorts(id) ON DELETE CASCADE,
    script TEXT,
    prompts_json TEXT NOT NULL DEFAULT '[]',
    assets_json TEXT NOT NULL DEFAULT '[]',
    transformations_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS short_approvals (
    short_id INTEGER NOT NULL REFERENCES shorts(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL CHECK(dimension IN ('rights', 'policy', 'quality')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    note TEXT,
    reviewed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (short_id, dimension)
);

CREATE TABLE IF NOT EXISTS media_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    short_id INTEGER REFERENCES shorts(id) ON DELETE SET NULL,
    short_name TEXT NOT NULL,
    action TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO short_approvals(short_id, dimension, status)
SELECT id, 'rights', 'pending' FROM shorts;
INSERT OR IGNORE INTO short_approvals(short_id, dimension, status)
SELECT id, 'policy', 'pending' FROM shorts;
INSERT OR IGNORE INTO short_approvals(short_id, dimension, status)
SELECT id, 'quality', 'pending' FROM shorts;

CREATE TRIGGER IF NOT EXISTS create_short_approvals
AFTER INSERT ON shorts
BEGIN
    INSERT OR IGNORE INTO short_approvals(short_id, dimension, status) VALUES (NEW.id, 'rights', 'pending');
    INSERT OR IGNORE INTO short_approvals(short_id, dimension, status) VALUES (NEW.id, 'policy', 'pending');
    INSERT OR IGNORE INTO short_approvals(short_id, dimension, status) VALUES (NEW.id, 'quality', 'pending');
END;

CREATE VIEW IF NOT EXISTS publication_ready_shorts AS
SELECT sc.*
FROM shorts_catalog sc
WHERE NOT EXISTS (
    SELECT 1
    FROM short_approvals approval
    WHERE approval.short_id = sc.id
      AND approval.status <> 'approved'
)
AND 3 = (
    SELECT COUNT(*)
    FROM short_approvals approval
    WHERE approval.short_id = sc.id
      AND approval.status = 'approved'
);

CREATE INDEX IF NOT EXISTS idx_short_sources_source_id ON short_sources(source_id);
CREATE INDEX IF NOT EXISTS idx_short_approvals_status ON short_approvals(status, dimension);
CREATE INDEX IF NOT EXISTS idx_media_audit_short ON media_audit_log(short_id, created_at);
