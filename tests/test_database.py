import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import database


def configure_databases(tmp_path: Path, monkeypatch):
    migrations = Path(database.__file__).parent / "migrations"
    media = tmp_path / "media.sqlite"
    publishing = tmp_path / "publishing.sqlite"
    monkeypatch.setattr(
        database,
        "DATABASES",
        {
            "media": (media, migrations / "media"),
            "publishing": (publishing, migrations / "publishing"),
        },
    )
    monkeypatch.setattr(
        database,
        "settings",
        SimpleNamespace(backup_root=tmp_path / "backups", workflow_export_root=tmp_path / "workflows"),
    )
    return media, publishing


def test_migrations_create_schema_and_are_idempotent(tmp_path, monkeypatch):
    media, publishing = configure_databases(tmp_path, monkeypatch)

    first = database.migrate_all(backup_before=False)
    second = database.migrate_all(backup_before=False)

    assert [item["version"] for item in first] == [2, 1]
    assert all(item["applied"] == [] for item in second)
    with sqlite3.connect(media) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'shorts_catalog'").fetchone()
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'publication_ready_shorts'").fetchone()
    with sqlite3.connect(publishing) as conn:
        privacy = conn.execute("PRAGMA table_info(youtube_accounts)").fetchall()
        assert "default_privacy_status" in {row[1] for row in privacy}


def test_migration_preserves_existing_rows_and_creates_backup(tmp_path, monkeypatch):
    media, _ = configure_databases(tmp_path, monkeypatch)
    with sqlite3.connect(media) as conn:
        conn.execute(
            "CREATE TABLE serials (id INTEGER PRIMARY KEY, serial_slug TEXT UNIQUE, serial_name TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute("INSERT INTO serials(id, serial_slug, serial_name) VALUES (1, 'kept', 'Kept')")

    result = database.migrate_database("media", backup_before=True)

    assert result["backup"] is not None
    with sqlite3.connect(media) as conn:
        assert conn.execute("SELECT serial_name FROM serials WHERE id = 1").fetchone()[0] == "Kept"


def test_backup_restore_verifies_checksum(tmp_path, monkeypatch):
    media, publishing = configure_databases(tmp_path, monkeypatch)
    database.migrate_all(backup_before=False)
    manifest = database.create_backup(label="test", root=tmp_path / "archives")
    destination = tmp_path / "restored"

    result = database.restore_backup(Path(manifest["backupDir"]), destination)

    assert len(result["restored"]) == 2
    for restored in map(Path, result["restored"]):
        expected = next(item["sha256"] for item in manifest["files"] if Path(item["path"]).name == restored.name)
        assert hashlib.sha256(restored.read_bytes()).hexdigest() == expected
    stored = json.loads((Path(manifest["backupDir"]) / "manifest.json").read_text())
    assert stored["files"]
    assert media.exists() and publishing.exists()
