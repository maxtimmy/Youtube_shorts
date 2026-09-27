from database import connect, migrate_all
from config import settings


def load_fixtures(force: bool = False) -> dict:
    migrate_all(backup_before=False)
    with connect(settings.media_db) as media:
        existing = media.execute("SELECT COUNT(*) AS count FROM serials").fetchone()["count"]
        if existing and not force:
            raise RuntimeError("Media database is not empty; use --force to add fixtures anyway")
        media.execute("INSERT OR IGNORE INTO serials(serial_slug, serial_name) VALUES ('demo-series', 'Demo Series')")
        serial_id = media.execute("SELECT id FROM serials WHERE serial_slug = 'demo-series'").fetchone()["id"]
        media.execute(
            "INSERT OR IGNORE INTO episodes(serial_id, episode_base_name, episode_number, file_name, chunk_count) VALUES (?, 'Demo01', 1, 'Demo01.mp4', 1)",
            (serial_id,),
        )
        episode_id = media.execute(
            "SELECT id FROM episodes WHERE serial_id = ? AND episode_base_name = 'Demo01'", (serial_id,)
        ).fetchone()["id"]
        media.execute(
            "INSERT OR IGNORE INTO shorts(episode_id, short_name, short_part, duration, final_path, score, text) VALUES (?, 'Demo01_part01', 1, 24.0, '/work/output/demo/Demo01_part01.mp4', 0.9, 'Synthetic fixture content')",
            (episode_id,),
        )
    with connect(settings.publish_db) as publishing:
        existing = publishing.execute("SELECT COUNT(*) AS count FROM youtube_accounts").fetchone()["count"]
        if existing and not force:
            raise RuntimeError("Publishing database is not empty; use --force to add fixtures anyway")
        publishing.execute(
            "INSERT OR IGNORE INTO youtube_accounts(account_slug, account_name, youtube_credential_name, youtube_credential_id, channel_title) VALUES ('demo-channel', 'Demo Channel', 'demo-credential', 'demo-credential-id', 'Demo Channel')"
        )
        account_id = publishing.execute(
            "SELECT id FROM youtube_accounts WHERE account_slug = 'demo-channel'"
        ).fetchone()["id"]
        publishing.execute(
            "INSERT OR IGNORE INTO account_active_serials(account_id, serial_slug, serial_name) VALUES (?, 'demo-series', 'Demo Series')",
            (account_id,),
        )
        publishing.execute(
            "INSERT OR IGNORE INTO account_schedule_slots(account_id, slot_time) VALUES (?, '12:00')", (account_id,)
        )
        publishing.execute(
            "INSERT OR IGNORE INTO youtube_publication_history(account_id, short_name, serial_slug, episode_base_name, youtube_title, status, privacy_status) VALUES (?, 'Demo01_part01', 'demo-series', 'Demo01', 'Demo original short', 'scheduled', 'private')",
            (account_id,),
        )
    return {"loaded": True, "media": str(settings.media_db), "publishing": str(settings.publish_db)}
