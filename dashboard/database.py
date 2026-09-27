from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import settings


DATABASES = {
    "media": (settings.media_db, Path(__file__).parent / "migrations" / "media"),
    "publishing": (settings.publish_db, Path(__file__).parent / "migrations" / "publishing"),
}

REQUIRED_COLUMNS = {
    "media": {
        "serials": {"id", "serial_slug", "serial_name"},
        "episodes": {"id", "serial_id", "episode_base_name", "episode_number", "file_name", "chunk_count"},
        "shorts": {
            "id",
            "episode_id",
            "short_name",
            "short_part",
            "duration",
            "final_path",
            "clip_path",
            "score",
            "text",
        },
    },
    "publishing": {
        "youtube_accounts": {
            "id",
            "account_slug",
            "account_name",
            "youtube_credential_name",
            "youtube_credential_id",
            "default_privacy_status",
            "is_active",
        },
        "account_active_serials": {"account_id", "serial_slug", "serial_name"},
        "account_schedule_slots": {"account_id", "slot_time", "is_active"},
        "youtube_account_cooldowns": {
            "id",
            "account_id",
            "reason_code",
            "blocked_until",
            "is_active",
        },
        "youtube_publication_history": {
            "id",
            "account_id",
            "short_name",
            "serial_slug",
            "status",
            "privacy_status",
            "uploaded_at",
            "publish_at_utc",
        },
    },
}


class MigrationError(RuntimeError):
    pass


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _migration_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))


def schema_version(path: Path) -> int:
    if not path.exists():
        return 0
    with connect(path) as conn:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if not found:
            return 0
        row = conn.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations").fetchone()
        return int(row["version"])


def validate_schema(name: str, path: Path) -> list[str]:
    errors = []
    with connect(path) as conn:
        for table, expected in REQUIRED_COLUMNS[name].items():
            actual = {row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
            if not actual:
                errors.append(f"missing table: {table}")
                continue
            missing = sorted(expected - actual)
            if missing:
                errors.append(f"{table} missing columns: {', '.join(missing)}")
    return errors


def backup_database(path: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {"source": str(path), "path": str(destination), "sha256": digest, "bytes": destination.stat().st_size}


def create_backup(label: str | None = None, root: Path | None = None) -> dict:
    stamp = label or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = (root or settings.backup_root) / stamp
    if backup_dir.exists():
        raise FileExistsError(f"Backup already exists: {backup_dir}")
    backup_dir.mkdir(parents=True)
    files = []
    for name, (path, _) in DATABASES.items():
        if path.exists():
            files.append({"name": name, **backup_database(path, backup_dir / path.name)})
    workflow_source = settings.workflow_export_root
    if workflow_source.exists():
        workflow_target = backup_dir / "workflows"
        shutil.copytree(workflow_source, workflow_target)
        for item in sorted(workflow_target.glob("*.json")):
            files.append(
                {
                    "name": f"workflow:{item.stem}",
                    "path": str(item),
                    "sha256": hashlib.sha256(item.read_bytes()).hexdigest(),
                    "bytes": item.stat().st_size,
                }
            )
    manifest = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "backupDir": str(backup_dir),
        "files": files,
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def restore_backup(backup_dir: Path, destination_root: Path) -> dict:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Backup manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored = []
    destination_root.mkdir(parents=True, exist_ok=True)
    for entry in manifest.get("files", []):
        source = Path(entry["path"])
        if not source.is_absolute():
            source = backup_dir / source
        if not source.exists():
            source = backup_dir / Path(entry["path"]).name
        if not source.exists() or source.is_dir():
            continue
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            raise MigrationError(f"Checksum mismatch: {source}")
        target = destination_root / source.name
        if target.exists():
            raise FileExistsError(f"Restore target exists: {target}")
        shutil.copy2(source, target)
        restored.append(str(target))
    return {"backupDir": str(backup_dir), "destination": str(destination_root), "restored": restored}


def migrate_database(name: str, backup_before: bool = True) -> dict:
    if name not in DATABASES:
        raise KeyError(name)
    path, migration_dir = DATABASES[name]
    migrations = _migration_files(migration_dir)
    current = schema_version(path)
    pending = [item for item in migrations if int(item.name.split("_", 1)[0]) > current]
    backup = None
    if pending and path.exists() and path.stat().st_size and backup_before:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = settings.backup_root / f"pre-migration-{name}-{stamp}" / path.name
        backup = backup_database(path, target)
    with connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = []
        for migration in pending:
            version = int(migration.name.split("_", 1)[0])
            sql = migration.read_text(encoding="utf-8")
            try:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                    (version, migration.name),
                )
            except sqlite3.DatabaseError as exc:
                conn.rollback()
                raise MigrationError(f"Migration failed: {migration.name}: {exc}") from exc
            applied.append(migration.name)
        conn.commit()
    errors = validate_schema(name, path)
    if errors:
        raise MigrationError(f"Schema validation failed for {name}: {'; '.join(errors)}")
    return {
        "database": name,
        "path": str(path),
        "version": schema_version(path),
        "applied": applied,
        "backup": backup,
    }


def migrate_all(backup_before: bool = True) -> list[dict]:
    return [migrate_database(name, backup_before=backup_before) for name in DATABASES]


def database_status() -> list[dict]:
    result = []
    for name, (path, migration_dir) in DATABASES.items():
        migrations = _migration_files(migration_dir)
        latest = max((int(item.name.split("_", 1)[0]) for item in migrations), default=0)
        current = schema_version(path)
        errors = validate_schema(name, path) if path.exists() else ["database does not exist"]
        result.append(
            {
                "name": name,
                "path": str(path),
                "exists": path.exists(),
                "version": current,
                "latestVersion": latest,
                "ready": current == latest and latest > 0 and not errors,
                "errors": errors,
            }
        )
    return result
