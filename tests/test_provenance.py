import sqlite3
from pathlib import Path

import database
import provenance


def migrated_media(tmp_path: Path, monkeypatch) -> Path:
    migrations = Path(database.__file__).parent / "migrations"
    media = tmp_path / "media.sqlite"
    publishing = tmp_path / "publishing.sqlite"
    monkeypatch.setattr(
        database,
        "DATABASES",
        {"media": (media, migrations / "media"), "publishing": (publishing, migrations / "publishing")},
    )
    database.migrate_all(backup_before=False)
    with database.connect(media) as conn:
        conn.execute("INSERT INTO serials(serial_slug, serial_name) VALUES ('demo', 'Demo')")
        serial_id = conn.execute("SELECT id FROM serials WHERE serial_slug = 'demo'").fetchone()[0]
        conn.execute(
            "INSERT INTO episodes(serial_id, episode_base_name, file_name) VALUES (?, 'Demo01', 'Demo01.mp4')",
            (serial_id,),
        )
        episode_id = conn.execute("SELECT id FROM episodes WHERE episode_base_name = 'Demo01'").fetchone()[0]
        conn.execute(
            "INSERT INTO shorts(episode_id, short_name, final_path) VALUES (?, 'Demo01_part01', '/tmp/demo.mp4')",
            (episode_id,),
        )
    return media


def test_existing_and_new_shorts_default_to_pending(tmp_path, monkeypatch):
    media = migrated_media(tmp_path, monkeypatch)

    review = provenance.get_review(media, "Demo01_part01")

    assert review["publicationReady"] is False
    assert review["missingApprovals"] == ["rights", "policy", "quality"]
    with sqlite3.connect(media) as conn:
        assert conn.execute("SELECT COUNT(*) FROM publication_ready_shorts").fetchone()[0] == 0


def test_review_crud_approval_gate_and_audit(tmp_path, monkeypatch):
    media = migrated_media(tmp_path, monkeypatch)
    payload = {
        "sources": [
            {
                "url": "https://example.com/reference",
                "author": "Example author",
                "platform": "Example",
                "license": "Permission recorded",
                "checkedAt": "2026-09-28",
                "allowedUses": ["inspiration"],
                "notes": "Do not copy footage",
                "useType": "reference",
            }
        ],
        "creative": {
            "script": "Original script",
            "prompts": ["Original visual prompt"],
            "assets": [{"name": "generated-background.png", "origin": "generated"}],
            "transformations": ["Original edit"],
        },
        "approvals": {
            "rights": {"status": "approved", "note": "Rights checked"},
            "policy": {"status": "approved", "note": "Policy checked"},
            "quality": {"status": "approved", "note": "Quality checked"},
        },
    }

    review = provenance.update_review(media, "Demo01_part01", payload)

    assert review["publicationReady"] is True
    assert review["sources"][0]["allowedUses"] == ["inspiration"]
    assert review["creative"]["script"] == "Original script"
    assert review["audit"][0]["action"] == "review.updated"
    with sqlite3.connect(media) as conn:
        assert conn.execute("SELECT COUNT(*) FROM publication_ready_shorts").fetchone()[0] == 1


def test_review_rejects_invalid_source_url(tmp_path, monkeypatch):
    media = migrated_media(tmp_path, monkeypatch)

    try:
        provenance.update_review(media, "Demo01_part01", {"sources": [{"url": "file:///private/video.mp4"}]})
    except provenance.ReviewValidationError as exc:
        assert "http or https" in str(exc)
    else:
        raise AssertionError("Invalid source URL was accepted")
