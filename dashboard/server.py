from __future__ import annotations

import json
import re
import shutil
import sqlite3
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from config import settings
from database import connect, database_status, migrate_all
from n8n_client import N8NError, client as n8n_client, post_webhook
from provenance import ReviewValidationError, get_review, missing_approvals, update_review

ROOT = settings.app_root
STATIC_DIR = ROOT / "static"
MEDIA_DB = settings.media_db
PUBLISH_DB = settings.publish_db
INPUT_ROOT = settings.input_root
EVENT_LOG_PATH = Path("/nonexistent/n8nEventLog.log")
HOST = settings.host
PORT = settings.port
MANAGED_WORKFLOWS = {
    "render": "Auto Shorts - Render Queue",
    "upload": "Auto Shorts - YouTube Upload",
}
WORKFLOW_IDS = {
    "render": settings.render_workflow_id,
    "upload": settings.upload_workflow_id,
}
CONTROL_STATE_PATH = Path("/work/temp/dashboard-control-state.json")
WORKFLOW_STEP_HINTS = {
    "render": [
        "Prepare Job",
        "Whisper Full Video",
        "Semantic Chunk Builder",
        "Load Chunk Manifest",
        "ffmpeg cut clips",
        "Build ASS Captions",
        "Render Shorts",
        "Sync Catalog After Render",
    ],
    "upload": [
        "Load Next Short",
        "Generate YouTube Metadata",
        "Read Short File",
        "Upload to YouTube",
        "Record Upload In DB",
    ],
}


def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9а-яё]+", "-", value, flags=re.IGNORECASE)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "unknown"


def titleize(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip())
    return (value[:1].upper() + value[1:]) if value else "Unknown"


def serial_prefix(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9А-Яа-яЁё]+", "", (value or "").strip())
    return cleaned or "Serial"


def serial_input_dir(serial_slug: str) -> Path:
    return INPUT_ROOT / serial_slug


def ensure_within_input_root(path: Path) -> Path:
    resolved_root = INPUT_ROOT.resolve()
    resolved_path = path.resolve()
    resolved_path.relative_to(resolved_root)
    return resolved_path


def input_serial_rows() -> list[dict]:
    if not INPUT_ROOT.exists():
        return []
    rows = []
    for child in sorted(INPUT_ROOT.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        mp4_count = len([item for item in child.iterdir() if item.is_file() and item.suffix.lower() == ".mp4"])
        rows.append(
            {
                "serial_slug": slugify(child.name),
                "serial_name": titleize(child.name),
                "input_dir_name": child.name,
                "input_dir_path": str(child),
                "input_episode_count": mp4_count,
            }
        )
    return rows


def next_episode_defaults(serial_slug: str, serial_name: str) -> dict:
    input_dir = serial_input_dir(serial_slug)
    input_dir.mkdir(parents=True, exist_ok=True)
    existing_files = sorted(
        [item for item in input_dir.iterdir() if item.is_file() and item.suffix.lower() == ".mp4"],
        key=lambda item: item.name.lower(),
    )
    next_number = len(existing_files) + 1
    default_stem = f"{serial_prefix(serial_name)}{next_number}"
    return {
        "inputDir": str(input_dir),
        "nextEpisodeNumber": next_number,
        "defaultEpisodeStem": default_stem,
        "existingEpisodeFileCount": len(existing_files),
    }


def db_conn(path: Path) -> sqlite3.Connection:
    return connect(path)


def fetch_json(sql: str, params: tuple = (), *, db: Path | None = None) -> list[dict]:
    conn = db_conn(db or PUBLISH_DB)
    cur = conn.cursor()
    rows = [dict(row) for row in cur.execute(sql, params).fetchall()]
    conn.close()
    return rows


def fetch_one(sql: str, params: tuple = (), *, db: Path | None = None) -> dict | None:
    conn = db_conn(db or PUBLISH_DB)
    cur = conn.cursor()
    row = cur.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None


def execute(sql: str, params: tuple = (), *, db: Path | None = None) -> None:
    conn = db_conn(db or PUBLISH_DB)
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    conn.close()


def fetch_all(sql: str, params: tuple = (), *, db: Path) -> list[dict]:
    conn = db_conn(db)
    cur = conn.cursor()
    rows = [dict(row) for row in cur.execute(sql, params).fetchall()]
    conn.close()
    return rows


PUBLISHED_STATUS_SQL = """
CASE
    WHEN h.status = 'published' THEN 'published'
    WHEN datetime(h.publish_at_utc) <= datetime('now')
    THEN 'published'
    ELSE h.status
END
""".strip()


def account_lookup(account_slug: str) -> dict | None:
    return fetch_one(
        """
        SELECT
            id,
            account_slug,
            account_name,
            youtube_credential_name,
            default_privacy_status,
            is_active
        FROM youtube_accounts
        WHERE account_slug <> 'default'
          AND (
              account_slug = ?
              OR account_name = ?
              OR youtube_credential_name = ?
          )
        LIMIT 1
        """,
        (account_slug, account_slug, account_slug),
    )


def media_summary() -> dict:
    conn = db_conn(MEDIA_DB)
    cur = conn.cursor()
    db_serials = [
        dict(row)
        for row in cur.execute(
            """
        SELECT
            s.serial_slug,
            s.serial_name,
            COUNT(DISTINCT e.id) AS episode_count,
            COUNT(DISTINCT sh.id) AS short_count
        FROM serials s
        LEFT JOIN episodes e ON e.serial_id = s.id
        LEFT JOIN shorts sh ON sh.episode_id = e.id
        GROUP BY s.id
        ORDER BY s.serial_name
        """
        ).fetchall()
    ]
    conn.close()

    serial_map = {
        row["serial_slug"]: {
            **row,
            "input_dir_name": row["serial_slug"],
        }
        for row in db_serials
    }
    for row in input_serial_rows():
        if row["serial_slug"] in serial_map:
            serial_map[row["serial_slug"]]["input_dir_name"] = row["input_dir_name"]
            continue
        serial_map[row["serial_slug"]] = {
            "serial_slug": row["serial_slug"],
            "serial_name": row["serial_name"],
            "episode_count": 0,
            "short_count": 0,
            "input_dir_name": row["input_dir_name"],
        }

    serials = sorted(serial_map.values(), key=lambda item: item["serial_name"].lower())
    overview = {
        "serialCount": len(serials),
        "episodeCount": sum(int(item["episode_count"] or 0) for item in serials),
        "shortCount": sum(int(item["short_count"] or 0) for item in serials),
        "serials": serials,
    }
    return overview


def serial_detail(serial_slug: str) -> dict:
    conn = db_conn(MEDIA_DB)
    cur = conn.cursor()
    serial = cur.execute(
        "SELECT serial_slug, serial_name FROM serials WHERE serial_slug = ?",
        (serial_slug,),
    ).fetchone()
    if not serial:
        for input_serial in input_serial_rows():
            if input_serial["serial_slug"] == serial_slug:
                conn.close()
                defaults = next_episode_defaults(serial_slug, input_serial["serial_name"])
                return {
                    "serial": {
                        "serial_slug": input_serial["serial_slug"],
                        "serial_name": input_serial["serial_name"],
                        "input_dir_name": input_serial["input_dir_name"],
                    },
                    "episodes": [],
                    "uploadDefaults": defaults,
                }
        conn.close()
        raise KeyError(serial_slug)

    episodes = []
    for row in cur.execute(
        """
        SELECT
            e.episode_base_name,
            e.episode_number,
            e.file_name,
            e.chunk_count,
            COUNT(sh.id) AS short_count
        FROM episodes e
        LEFT JOIN shorts sh ON sh.episode_id = e.id
        JOIN serials s ON s.id = e.serial_id
        WHERE s.serial_slug = ?
        GROUP BY e.id
        ORDER BY COALESCE(e.episode_number, 999999), e.episode_base_name
        """,
        (serial_slug,),
    ).fetchall():
        episode = dict(row)
        episode["shorts"] = [
            dict(short_row)
            for short_row in cur.execute(
                """
            SELECT
                sh.short_name,
                sh.short_part,
                sh.duration,
                sh.final_path,
                sh.clip_path,
                sh.score,
                sh.text,
                CASE WHEN ready.id IS NOT NULL THEN 1 ELSE 0 END AS publication_ready,
                COALESCE((
                    SELECT GROUP_CONCAT(approval.dimension)
                    FROM short_approvals approval
                    WHERE approval.short_id = sh.id
                      AND approval.status <> 'approved'
                ), 'rights,policy,quality') AS missing_approvals
            FROM shorts sh
            JOIN episodes e ON e.id = sh.episode_id
            JOIN serials s ON s.id = e.serial_id
            LEFT JOIN publication_ready_shorts ready ON ready.id = sh.id
            WHERE s.serial_slug = ?
              AND e.episode_base_name = ?
            ORDER BY sh.short_part
            """,
                (serial_slug, row["episode_base_name"]),
            ).fetchall()
        ]
        episodes.append(episode)

    serial_payload = dict(serial)
    matching_input = next((item for item in input_serial_rows() if item["serial_slug"] == serial_slug), None)
    if matching_input:
        serial_payload["input_dir_name"] = matching_input["input_dir_name"]
    defaults = next_episode_defaults(serial_slug, serial_payload["serial_name"])
    conn.close()
    return {"serial": serial_payload, "episodes": episodes, "uploadDefaults": defaults}


def delete_serial(serial_slug: str) -> dict:
    summary = media_summary()
    serial = next((item for item in summary["serials"] if item["serial_slug"] == serial_slug), None)
    input_dir = serial_input_dir(serial_slug)
    input_exists = input_dir.exists()

    if not serial and not input_exists:
        raise KeyError(serial_slug)

    media_deleted = {"serials": 0, "episodes": 0, "shorts": 0}
    publish_deleted = {"history": 0, "activeSerials": 0}

    media_conn = db_conn(MEDIA_DB)
    media_cur = media_conn.cursor()
    serial_row = media_cur.execute(
        "SELECT id, serial_slug, serial_name FROM serials WHERE serial_slug = ?",
        (serial_slug,),
    ).fetchone()
    if serial_row:
        episode_rows = media_cur.execute(
            "SELECT id FROM episodes WHERE serial_id = ?",
            (serial_row["id"],),
        ).fetchall()
        episode_ids = [row["id"] for row in episode_rows]
        if episode_ids:
            placeholders = ",".join("?" for _ in episode_ids)
            media_deleted["shorts"] = media_cur.execute(
                f"DELETE FROM shorts WHERE episode_id IN ({placeholders})",
                tuple(episode_ids),
            ).rowcount
        media_deleted["episodes"] = media_cur.execute(
            "DELETE FROM episodes WHERE serial_id = ?",
            (serial_row["id"],),
        ).rowcount
        media_deleted["serials"] = media_cur.execute(
            "DELETE FROM serials WHERE id = ?",
            (serial_row["id"],),
        ).rowcount
        media_conn.commit()
    media_conn.close()

    publish_conn = db_conn(PUBLISH_DB)
    publish_cur = publish_conn.cursor()
    publish_deleted["history"] = publish_cur.execute(
        "DELETE FROM youtube_publication_history WHERE serial_slug = ?",
        (serial_slug,),
    ).rowcount
    publish_deleted["activeSerials"] = publish_cur.execute(
        "DELETE FROM account_active_serials WHERE serial_slug = ?",
        (serial_slug,),
    ).rowcount
    publish_conn.commit()
    publish_conn.close()

    folder_deleted = False
    if input_exists:
        resolved_dir = ensure_within_input_root(input_dir)
        shutil.rmtree(resolved_dir)
        folder_deleted = not resolved_dir.exists()

    return {
        "serialSlug": serial_slug,
        "serialName": (serial or {}).get("serial_name") or titleize(serial_slug),
        "folderDeleted": folder_deleted,
        "mediaDeleted": media_deleted,
        "publishDeleted": publish_deleted,
    }


def account_rows() -> list[dict]:
    conn = db_conn(PUBLISH_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("ATTACH DATABASE ? AS media", (str(MEDIA_DB),))
    rows = [
        dict(row)
        for row in cur.execute(
            """
        SELECT
            a.id,
            a.account_slug,
            a.account_name,
            a.youtube_credential_name,
            a.channel_title,
            a.default_privacy_status,
            a.is_active,
            s.serial_slug AS active_serial_slug,
            s.serial_name AS active_serial_name,
            c.reason_code AS cooldown_reason,
            c.reason_message AS cooldown_message,
            c.blocked_until AS cooldown_until,
            CASE WHEN c.id IS NOT NULL AND c.is_active = 1 AND datetime(c.blocked_until) > datetime('now')
                 THEN 1 ELSE 0 END AS is_blocked,
            (SELECT COUNT(*) FROM youtube_publication_history h WHERE h.account_id = a.id) AS uploads_total,
            (SELECT COUNT(*) FROM youtube_publication_history h WHERE h.account_id = a.id AND ({published_status_sql}) = 'scheduled') AS scheduled_total,
            (SELECT COUNT(*) FROM youtube_publication_history h WHERE h.account_id = a.id AND ({published_status_sql}) = 'published') AS published_total,
            (
                SELECT COUNT(*)
                FROM media.shorts_catalog sc
                WHERE sc.serial_slug = s.serial_slug
            ) AS active_serial_short_total,
            (
                SELECT COUNT(*)
                FROM media.publication_ready_shorts sc
                WHERE sc.serial_slug = s.serial_slug
                  AND NOT EXISTS (
                      SELECT 1
                      FROM youtube_publication_history h
                      WHERE h.account_id = a.id
                        AND h.short_name = sc.short_name
                  )
            ) AS remaining_uploadable_total
        FROM youtube_accounts a
        LEFT JOIN account_active_serials s ON s.account_id = a.id
        LEFT JOIN youtube_account_cooldowns c
          ON c.account_id = a.id
         AND c.is_active = 1
         AND datetime(c.blocked_until) > datetime('now')
        WHERE a.account_slug <> 'default'
        ORDER BY a.is_active DESC, a.account_name
        """.format(published_status_sql=PUBLISHED_STATUS_SQL)
        ).fetchall()
    ]
    conn.close()
    return rows


def list_youtube_credentials() -> list[dict]:
    rows = [
        {"id": str(item["id"]), "name": str(item["name"]), "type": "youTubeOAuth2Api"}
        for item in settings.youtube_credentials()
    ]
    assigned_rows = fetch_json(
        """
        SELECT youtube_credential_id, account_slug, account_name
        FROM youtube_accounts
        WHERE youtube_credential_id IS NOT NULL
          AND TRIM(COALESCE(youtube_credential_id, '')) <> ''
        """
    )
    assigned_map = {str(row["youtube_credential_id"]): row for row in assigned_rows}
    for row in rows:
        assigned = assigned_map.get(str(row["id"]))
        row["assigned_account_slug"] = assigned["account_slug"] if assigned else None
        row["assigned_account_name"] = assigned["account_name"] if assigned else None
    return rows


def upload_workflow_branch_accounts() -> list[dict]:
    return fetch_json(
        """
        SELECT
            id,
            account_slug,
            account_name,
            youtube_credential_name,
            youtube_credential_id
        FROM youtube_accounts
        WHERE account_slug <> 'default'
          AND is_active = 1
          AND youtube_credential_id IS NOT NULL
          AND TRIM(COALESCE(youtube_credential_id, '')) <> ''
        ORDER BY id
        """
    )


def rebuild_upload_workflow() -> dict:
    workflow_id = WORKFLOW_IDS["upload"]
    if not workflow_id:
        raise KeyError("Upload workflow is not configured")
    row = n8n_client.get_workflow(workflow_id)
    if not row:
        raise KeyError("Upload workflow not found")

    nodes = row.get("nodes") or []
    if isinstance(nodes, str):
        nodes = json.loads(nodes)
    workflow_settings = row.get("settings") or {}

    def node_by_name(name: str) -> dict:
        for node in nodes:
            if node.get("name") == name:
                return node
        raise KeyError(name)

    base_names = {
        "Schedule Trigger",
        "Manual Webhook - Upload",
        "Load Next Short",
        "Generate YouTube Metadata",
        "Record Upload In DB",
    }
    rebuilt_nodes = [node for node in nodes if node.get("name") in base_names]

    read_template = next(node for node in nodes if node.get("name", "").startswith("Read Short File - "))
    upload_template = next(node for node in nodes if node.get("name", "").startswith("Upload to YouTube - "))
    merge_template = next(node for node in nodes if node.get("name", "").startswith("Merge Upload Result - "))
    accounts = upload_workflow_branch_accounts()

    branch_specs = []
    start_x = 864
    start_y = -224
    step_y = 272

    for index, account in enumerate(accounts):
        branch_y = start_y + (index * step_y)
        account_slug = str(account["account_slug"])
        account_name = str(account["account_name"])

        if_node = {
            "parameters": {
                "conditions": {
                    "options": {
                        "caseSensitive": True,
                        "leftValue": "",
                        "typeValidation": "strict",
                        "version": 2,
                    },
                    "conditions": [
                        {
                            "id": str(uuid.uuid4()),
                            "leftValue": "={{ $json.accountSlug || '' }}",
                            "rightValue": account_slug,
                            "operator": {"type": "string", "operation": "equals"},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.2,
            "position": [start_x, branch_y + 96],
            "id": str(uuid.uuid4()),
            "name": f"If Account - {account_name}",
        }

        read_node = json.loads(json.dumps(read_template))
        read_node["id"] = str(uuid.uuid4())
        read_node["name"] = f"Read Short File - {account_name}"
        read_node["position"] = [1152, branch_y]

        upload_node = json.loads(json.dumps(upload_template))
        upload_node["id"] = str(uuid.uuid4())
        upload_node["name"] = f"Upload to YouTube - {account_name}"
        upload_node["position"] = [1408, branch_y]
        upload_node["credentials"] = {
            "youTubeOAuth2Api": {
                "id": str(account["youtube_credential_id"]),
                "name": str(account["youtube_credential_name"]),
            }
        }

        merge_node = json.loads(json.dumps(merge_template))
        merge_node["id"] = str(uuid.uuid4())
        merge_node["name"] = f"Merge Upload Result - {account_name}"
        merge_node["position"] = [1664, branch_y]

        rebuilt_nodes.extend([if_node, read_node, upload_node, merge_node])
        branch_specs.append(
            {
                "if": if_node["name"],
                "read": read_node["name"],
                "upload": upload_node["name"],
                "merge": merge_node["name"],
            }
        )

    connections: dict[str, dict] = {
        "Schedule Trigger": {"main": [[{"node": "Load Next Short", "type": "main", "index": 0}]]},
        "Manual Webhook - Upload": {"main": [[{"node": "Load Next Short", "type": "main", "index": 0}]]},
        "Load Next Short": {"main": [[{"node": "Generate YouTube Metadata", "type": "main", "index": 0}]]},
        "Record Upload In DB": {"main": [[]]},
    }

    if branch_specs:
        connections["Generate YouTube Metadata"] = {
            "main": [[{"node": branch_specs[0]["if"], "type": "main", "index": 0}]]
        }
    else:
        connections["Generate YouTube Metadata"] = {"main": [[]]}

    for index, branch in enumerate(branch_specs):
        false_target = branch_specs[index + 1]["if"] if index + 1 < len(branch_specs) else None
        connections[branch["if"]] = {
            "main": [
                [{"node": branch["read"], "type": "main", "index": 0}],
                ([{"node": false_target, "type": "main", "index": 0}] if false_target else []),
            ]
        }
        connections[branch["read"]] = {
            "main": [
                [
                    {"node": branch["upload"], "type": "main", "index": 0},
                    {"node": branch["merge"], "type": "main", "index": 0},
                ]
            ]
        }
        connections[branch["upload"]] = {"main": [[{"node": branch["merge"], "type": "main", "index": 1}]]}
        connections[branch["merge"]] = {"main": [[{"node": "Record Upload In DB", "type": "main", "index": 0}]]}

    updated = n8n_client.update_workflow(
        workflow_id,
        {
            "name": row.get("name") or MANAGED_WORKFLOWS["upload"],
            "nodes": rebuilt_nodes,
            "connections": connections,
            "settings": workflow_settings,
        },
    )
    return {
        "versionId": updated.get("versionId"),
        "accounts": [item["account_name"] for item in accounts],
        "branchCount": len(accounts),
    }


def create_account(payload: dict) -> dict:
    account_name = str(payload.get("accountName") or "").strip()
    credential_id = str(payload.get("credentialId") or "").strip()
    serial_slug = str(payload.get("serialSlug") or "").strip()
    slots = sorted(
        {normalize for normalize in (str(slot).strip() for slot in (payload.get("slots") or [])) if normalize}
    )

    if not account_name:
        raise ValueError("accountName is required")
    if not credential_id:
        raise ValueError("credentialId is required")
    if not serial_slug:
        raise ValueError("serialSlug is required")

    credential = next((item for item in list_youtube_credentials() if str(item["id"]) == credential_id), None)
    if not credential:
        raise KeyError("Credential not found")

    serial = fetch_one(
        "SELECT serial_slug, serial_name FROM serials WHERE serial_slug = ?", (serial_slug,), db=MEDIA_DB
    )
    if not serial:
        raise KeyError("Serial not found")

    account_slug = slugify(account_name)
    if account_slug == "default":
        account_slug = f"account-{uuid.uuid4().hex[:6]}"

    existing_slug = fetch_one("SELECT id FROM youtube_accounts WHERE account_slug = ?", (account_slug,))
    if existing_slug:
        raise ValueError("Account with this slug already exists")

    existing_credential = fetch_one(
        "SELECT id, account_name FROM youtube_accounts WHERE youtube_credential_id = ?",
        (credential_id,),
    )
    if existing_credential:
        raise ValueError(f'Credential уже привязан к аккаунту "{existing_credential["account_name"]}"')

    if not slots:
        slots = ["08:50", "11:50", "13:50", "15:50", "18:50"]

    conn = db_conn(PUBLISH_DB)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO youtube_accounts (
            account_slug,
            account_name,
            youtube_credential_name,
            timezone,
            schedule_offset,
            default_privacy_status,
            is_active,
            created_at,
            updated_at,
            youtube_credential_id,
            channel_title
        )
        VALUES (?, ?, ?, 'Europe/Moscow', '+03:00', 'private', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)
        """,
        (account_slug, account_name, credential["name"], credential_id, account_name),
    )
    account_id = cur.lastrowid
    cur.execute(
        """
        INSERT INTO account_active_serials (account_id, serial_slug, serial_name, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (account_id, serial["serial_slug"], serial["serial_name"]),
    )
    for slot in slots:
        cur.execute(
            """
            INSERT INTO account_schedule_slots (account_id, slot_time, is_active, created_at, updated_at)
            VALUES (?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (account_id, slot),
        )
    conn.commit()
    conn.close()

    try:
        workflow_state = rebuild_upload_workflow()
    except Exception:
        cleanup_conn = db_conn(PUBLISH_DB)
        cleanup_cur = cleanup_conn.cursor()
        cleanup_cur.execute("DELETE FROM account_schedule_slots WHERE account_id = ?", (account_id,))
        cleanup_cur.execute("DELETE FROM account_active_serials WHERE account_id = ?", (account_id,))
        cleanup_cur.execute("DELETE FROM youtube_accounts WHERE id = ?", (account_id,))
        cleanup_conn.commit()
        cleanup_conn.close()
        raise
    return {
        "account": {
            "id": account_id,
            "account_slug": account_slug,
            "account_name": account_name,
            "youtube_credential_id": credential_id,
            "youtube_credential_name": credential["name"],
            "serial_slug": serial["serial_slug"],
            "serial_name": serial["serial_name"],
            "slots": slots,
        },
        "workflow": workflow_state,
    }


def account_history(account_slug: str) -> dict:
    account_ref = account_lookup(account_slug)
    if not account_ref:
        raise KeyError(account_slug)

    account = fetch_one(
        """
        SELECT
            a.id,
            a.account_slug,
            a.account_name,
            a.youtube_credential_name,
            a.default_privacy_status,
            a.is_active,
            s.serial_slug AS active_serial_slug,
            s.serial_name AS active_serial_name
        FROM youtube_accounts a
        LEFT JOIN account_active_serials s ON s.account_id = a.id
        WHERE a.id = ?
        """,
        (account_ref["id"],),
    )

    history = fetch_json(
        """
        SELECT
            short_name,
            serial_slug,
            episode_base_name,
            youtube_title,
            youtube_video_id,
            youtube_url,
            privacy_status,
            uploaded_at,
            publish_at_local,
            publish_at_utc,
            publish_date,
            publish_time,
            {published_status_sql} AS status
        FROM youtube_publication_history h
        WHERE h.account_id = ?
        ORDER BY datetime(h.uploaded_at) DESC, h.id DESC
        LIMIT 200
        """.format(published_status_sql=PUBLISHED_STATUS_SQL),
        (account["id"],),
    )
    cooldowns = fetch_json(
        """
        SELECT
            id,
            reason_code,
            reason_message,
            source_workflow_name,
            source_node_name,
            blocked_at,
            blocked_until,
            is_active,
            CASE
                WHEN is_active = 1 AND datetime(blocked_until) > datetime('now') THEN 1
                ELSE 0
            END AS is_current_active
        FROM youtube_account_cooldowns
        WHERE account_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 50
        """,
        (account["id"],),
    )
    schedule_slots = fetch_json(
        """
        SELECT slot_time, is_active
        FROM account_schedule_slots
        WHERE account_id = ?
        ORDER BY slot_time
        """,
        (account["id"],),
    )
    return {"account": account, "history": history, "cooldowns": cooldowns, "scheduleSlots": schedule_slots}


def dashboard_payload() -> dict:
    media = media_summary()
    accounts = account_rows()
    latest_uploads = fetch_json(
        """
        SELECT
            a.account_slug,
            a.account_name,
            h.short_name,
            h.serial_slug,
            h.episode_base_name,
            h.youtube_title,
            h.youtube_url,
            {published_status_sql} AS status,
            h.uploaded_at,
            h.publish_at_local
        FROM youtube_publication_history h
        JOIN youtube_accounts a ON a.id = h.account_id
        WHERE a.account_slug <> 'default'
        ORDER BY datetime(h.uploaded_at) DESC, h.id DESC
        LIMIT 20
        """.format(published_status_sql=PUBLISHED_STATUS_SQL)
    )
    publication_calendar = fetch_json(
        """
        SELECT
            a.account_slug,
            a.account_name,
            h.short_name,
            h.serial_slug,
            h.episode_base_name,
            h.youtube_title,
            h.youtube_url,
            h.publish_at_local,
            h.publish_at_utc,
            h.publish_date,
            h.publish_time,
            h.uploaded_at,
            {published_status_sql} AS status
        FROM youtube_publication_history h
        JOIN youtube_accounts a ON a.id = h.account_id
        WHERE a.account_slug <> 'default'
        ORDER BY datetime(h.publish_at_utc) ASC, a.account_name ASC, h.id ASC
        LIMIT 180
        """.format(published_status_sql=PUBLISHED_STATUS_SQL)
    )
    active_cooldowns = fetch_json(
        """
        SELECT
            a.account_slug,
            a.account_name,
            c.reason_code,
            c.reason_message,
            c.blocked_until,
            c.source_node_name
        FROM youtube_account_cooldowns c
        JOIN youtube_accounts a ON a.id = c.account_id
        WHERE c.is_active = 1
          AND a.account_slug <> 'default'
          AND datetime(c.blocked_until) > datetime('now')
        ORDER BY datetime(c.blocked_until)
        """
    )
    return {
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "media": media,
        "accounts": accounts,
        "latestUploads": latest_uploads,
        "publicationCalendar": publication_calendar,
        "activeCooldowns": active_cooldowns,
    }


def refresh_google_access_token(credential_payload: dict) -> str:
    data = credential_payload.get("data") or {}
    oauth = data.get("oauthTokenData") or {}
    refresh_token = oauth.get("refresh_token")
    client_id = data.get("clientId")
    client_secret = data.get("clientSecret")
    if not refresh_token or not client_id or not client_secret:
        raise RuntimeError("YouTube credential is missing OAuth refresh data")

    token_body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=token_body,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("Google OAuth token refresh did not return access_token")
    return str(access_token)


def export_youtube_credential_decrypted(credential_id: str) -> dict:
    raise RuntimeError("Live YouTube analytics is disabled: the dashboard never reads or decrypts n8n credentials")


def youtube_api_get(access_token: str, path: str, params: dict) -> dict:
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None and value != ""})
    url = f"https://www.googleapis.com/youtube/v3/{path}"
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def as_int(value) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def youtube_analytics_payload() -> dict:
    accounts = fetch_json(
        """
        SELECT
            a.id,
            a.account_slug,
            a.account_name,
            a.youtube_credential_id,
            a.youtube_credential_name,
            s.serial_slug AS active_serial_slug,
            s.serial_name AS active_serial_name
        FROM youtube_accounts a
        LEFT JOIN account_active_serials s ON s.account_id = a.id
        WHERE a.account_slug <> 'default'
          AND a.is_active = 1
          AND a.youtube_credential_id IS NOT NULL
          AND TRIM(COALESCE(a.youtube_credential_id, '')) <> ''
        ORDER BY a.account_name
        """
    )

    account_cards: list[dict] = []
    totals = {
        "accounts": 0,
        "trackedVideos": 0,
        "trackedViews": 0,
        "trackedLikes": 0,
        "trackedComments": 0,
        "channelViews": 0,
        "channelSubscribers": 0,
        "channelVideoCount": 0,
    }

    for account in accounts:
        history_rows = fetch_json(
            """
            SELECT
                short_name,
                serial_slug,
                episode_base_name,
                youtube_title,
                youtube_video_id,
                youtube_url,
                uploaded_at,
                publish_at_local,
                publish_at_utc,
                publish_date,
                publish_time,
                {published_status_sql} AS status
            FROM youtube_publication_history h
            WHERE h.account_id = ?
              AND h.youtube_video_id IS NOT NULL
              AND TRIM(COALESCE(h.youtube_video_id, '')) <> ''
            ORDER BY datetime(COALESCE(h.publish_at_utc, h.uploaded_at)) DESC, h.id DESC
            LIMIT 200
            """.format(published_status_sql=PUBLISHED_STATUS_SQL),
            (account["id"],),
        )

        account_card = {
            "accountSlug": account["account_slug"],
            "accountName": account["account_name"],
            "activeSerialSlug": account.get("active_serial_slug"),
            "activeSerialName": account.get("active_serial_name"),
            "credentialName": account.get("youtube_credential_name"),
            "channel": None,
            "trackedSummary": {
                "videos": len(history_rows),
                "views": 0,
                "likes": 0,
                "comments": 0,
            },
            "topVideos": [],
            "recentVideos": [],
            "error": None,
        }

        try:
            credential = export_youtube_credential_decrypted(str(account["youtube_credential_id"]))
            access_token = refresh_google_access_token(credential)

            channel_response = youtube_api_get(
                access_token,
                "channels",
                {"part": "snippet,statistics", "mine": "true"},
            )
            channel_item = (channel_response.get("items") or [{}])[0]
            channel_stats = channel_item.get("statistics") or {}
            channel_snippet = channel_item.get("snippet") or {}
            account_card["channel"] = {
                "channelId": channel_item.get("id"),
                "title": channel_snippet.get("title") or account["account_name"],
                "thumbnail": ((channel_snippet.get("thumbnails") or {}).get("default") or {}).get("url"),
                "views": as_int(channel_stats.get("viewCount")),
                "subscribers": as_int(channel_stats.get("subscriberCount")),
                "videos": as_int(channel_stats.get("videoCount")),
            }

            video_map: dict[str, dict] = {}
            ordered_ids: list[str] = []
            for row in history_rows:
                video_id = str(row.get("youtube_video_id") or "").strip()
                if not video_id:
                    continue
                if video_id not in video_map:
                    video_map[video_id] = dict(row)
                    ordered_ids.append(video_id)

            stats_by_video: dict[str, dict] = {}
            for index in range(0, len(ordered_ids), 50):
                chunk = ordered_ids[index : index + 50]
                response = youtube_api_get(
                    access_token,
                    "videos",
                    {"part": "statistics,snippet", "id": ",".join(chunk)},
                )
                for item in response.get("items") or []:
                    stats_by_video[item.get("id")] = item

            enriched_videos = []
            for video_id in ordered_ids:
                base = video_map[video_id]
                live = stats_by_video.get(video_id) or {}
                statistics = live.get("statistics") or {}
                snippet = live.get("snippet") or {}
                enriched = {
                    **base,
                    "youtube_video_id": video_id,
                    "youtube_title": base.get("youtube_title") or snippet.get("title") or base.get("short_name"),
                    "thumbnail": ((snippet.get("thumbnails") or {}).get("medium") or {}).get("url")
                    or ((snippet.get("thumbnails") or {}).get("default") or {}).get("url"),
                    "views": as_int(statistics.get("viewCount")),
                    "likes": as_int(statistics.get("likeCount")),
                    "comments": as_int(statistics.get("commentCount")),
                }
                enriched_videos.append(enriched)

            enriched_videos.sort(key=lambda item: item.get("views", 0), reverse=True)
            account_card["topVideos"] = enriched_videos[:6]
            account_card["trackedSummary"] = {
                "videos": len(enriched_videos),
                "views": sum(item["views"] for item in enriched_videos),
                "likes": sum(item["likes"] for item in enriched_videos),
                "comments": sum(item["comments"] for item in enriched_videos),
            }
            recent_videos = sorted(
                enriched_videos,
                key=lambda item: item.get("publish_at_utc") or item.get("uploaded_at") or "",
                reverse=True,
            )
            account_card["recentVideos"] = recent_videos[:10]

            totals["accounts"] += 1
            totals["trackedVideos"] += account_card["trackedSummary"]["videos"]
            totals["trackedViews"] += account_card["trackedSummary"]["views"]
            totals["trackedLikes"] += account_card["trackedSummary"]["likes"]
            totals["trackedComments"] += account_card["trackedSummary"]["comments"]
            totals["channelViews"] += (account_card["channel"] or {}).get("views", 0)
            totals["channelSubscribers"] += (account_card["channel"] or {}).get("subscribers", 0)
            totals["channelVideoCount"] += (account_card["channel"] or {}).get("videos", 0)
        except Exception as exc:
            account_card["error"] = str(exc)

        account_cards.append(account_card)

    return {
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "summary": totals,
        "accounts": account_cards,
    }


def extract_error_details(raw_data: str | None) -> dict:
    text = raw_data or ""
    try:
        compact = json.loads(text)
        if isinstance(compact, list) and compact:

            def resolve(value, seen: set[int] | None = None):
                if seen is None:
                    seen = set()
                if isinstance(value, str) and value.isdigit():
                    index = int(value)
                    if 0 <= index < len(compact):
                        if index in seen:
                            return value
                        return resolve(compact[index], seen | {index})
                if isinstance(value, list):
                    return [resolve(item, seen.copy()) for item in value]
                if isinstance(value, dict):
                    return {key: resolve(item, seen.copy()) for key, item in value.items()}
                return value

            root = resolve(compact[0])
            result = root.get("resultData", {}) if isinstance(root, dict) else {}
            error = result.get("error", {}) if isinstance(result, dict) else {}
            if isinstance(error, dict):
                return {
                    "lastNode": result.get("lastNodeExecuted"),
                    "description": error.get("description"),
                    "message": error.get("message"),
                    "httpCode": error.get("httpCode"),
                }
    except Exception:
        pass

    def match(pattern: str) -> str | None:
        found = re.search(pattern, text)
        if not found:
            return None
        value = found.group(1)
        return value.replace("\\n", "\n").replace('\\"', '"')

    return {
        "lastNode": match(r'"lastNodeExecuted":"([^"]+)"'),
        "description": match(r'"description":"([^"]+)"'),
        "message": match(r'"message":"([^"]+)"'),
        "httpCode": match(r'"httpCode":"([^"]+)"'),
    }


def upload_readiness_summary() -> dict:
    conn = db_conn(PUBLISH_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("ATTACH DATABASE ? AS media", (str(MEDIA_DB),))

    active_accounts = cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM youtube_accounts a
        WHERE a.is_active = 1
          AND a.account_slug <> 'default'
          AND a.youtube_credential_name IS NOT NULL
          AND TRIM(a.youtube_credential_name) != ''
        """
    ).fetchone()["c"]

    unblocked_accounts = cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM youtube_accounts a
        WHERE a.is_active = 1
          AND a.account_slug <> 'default'
          AND a.youtube_credential_name IS NOT NULL
          AND TRIM(a.youtube_credential_name) != ''
          AND NOT EXISTS (
              SELECT 1
              FROM youtube_account_cooldowns c
              WHERE c.account_id = a.id
                AND c.is_active = 1
                AND datetime(c.blocked_until) > datetime('now')
          )
        """
    ).fetchone()["c"]

    selectable_accounts = cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM youtube_accounts a
        JOIN account_active_serials s ON s.account_id = a.id
        WHERE a.is_active = 1
          AND a.account_slug <> 'default'
          AND a.youtube_credential_name IS NOT NULL
          AND TRIM(a.youtube_credential_name) != ''
          AND NOT EXISTS (
              SELECT 1
              FROM youtube_account_cooldowns c
              WHERE c.account_id = a.id
                AND c.is_active = 1
                AND datetime(c.blocked_until) > datetime('now')
          )
          AND EXISTS (
              SELECT 1
              FROM media.publication_ready_shorts sc
              WHERE sc.serial_slug = s.serial_slug
                AND sc.final_path IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM youtube_publication_history h
                    WHERE h.account_id = a.id
                      AND h.short_name = sc.short_name
                )
          )
        """
    ).fetchone()["c"]

    conn.close()

    if active_accounts == 0:
        return {
            "statusLabel": "Нет активных аккаунтов",
            "statusDetail": "Для загрузки нет включенных аккаунтов с credentials",
        }
    if unblocked_accounts == 0:
        return {
            "statusLabel": "Все аккаунты на timeout",
            "statusDetail": "Сейчас нет ни одного аккаунта, доступного для загрузки",
        }
    if selectable_accounts == 0:
        return {
            "statusLabel": "Нет доступных шортсов",
            "statusDetail": "Для активных аккаунтов сейчас нечего загружать",
        }
    return {"statusLabel": "Готов к запуску", "statusDetail": "Есть доступные аккаунты и шортсы для загрузки"}


def workflow_status_rows() -> list[dict]:
    workflows = n8n_client.list_workflows()
    executions = n8n_client.list_executions(limit=100)
    rows: list[dict] = []
    for workflow in workflows:
        workflow_id = str(workflow.get("id"))
        matching = [item for item in executions if str(item.get("workflowId")) == workflow_id]
        matching.sort(key=lambda item: int(item.get("id") or 0), reverse=True)
        running_count = sum(1 for item in matching if item.get("status") in {"running", "new"})
        row = {
            "id": workflow_id,
            "name": workflow.get("name") or workflow_id,
            "active": int(bool(workflow.get("active"))),
            "updatedAt": workflow.get("updatedAt"),
            "runningCount": running_count,
            "isRunning": running_count > 0,
            "lastExecution": matching[0] if matching else None,
            "canStop": False,
        }
        row["canRunManually"] = workflow["name"] in MANAGED_WORKFLOWS.values()
        row["runKey"] = next((key for key, value in MANAGED_WORKFLOWS.items() if value == workflow["name"]), None)
        rows.append(row)
    return rows


def read_recent_events(limit: int = 1200) -> list[dict]:
    if not EVENT_LOG_PATH.exists():
        return []
    try:
        lines = EVENT_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    events = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def normalize_step_name(run_key: str | None, node_name: str | None) -> str | None:
    if not node_name:
        return None
    if run_key == "upload":
        if node_name.startswith("Read Short File"):
            return "Read Short File"
        if node_name.startswith("Upload to YouTube"):
            return "Upload to YouTube"
        if node_name.startswith("If Credential"):
            return "Upload to YouTube"
        if node_name.startswith("Merge Upload Result"):
            return "Record Upload In DB"
    return node_name


def event_progress_by_execution() -> dict[int, dict]:
    events = read_recent_events()
    progress: dict[int, dict] = {}
    for event in events:
        payload = event.get("payload") or {}
        execution_id = payload.get("executionId")
        if execution_id is None:
            continue
        try:
            execution_id = int(execution_id)
        except Exception:
            continue
        entry = progress.setdefault(
            execution_id,
            {
                "lastEventName": None,
                "lastEventAt": None,
                "nodeName": None,
                "workflowName": payload.get("workflowName"),
                "source": payload.get("source"),
            },
        )
        entry["lastEventName"] = event.get("eventName")
        entry["lastEventAt"] = event.get("ts")
        if payload.get("nodeName"):
            entry["nodeName"] = payload.get("nodeName")
        if payload.get("source"):
            entry["source"] = payload.get("source")
    return progress


def workflow_runtime_from_events() -> dict[str, dict]:
    events = read_recent_events()
    runtime: dict[str, dict] = {}
    terminal_events = {"n8n.workflow.success", "n8n.workflow.failed"}
    for event in events:
        payload = event.get("payload") or {}
        workflow_id = payload.get("workflowId")
        execution_id = payload.get("executionId")
        if not workflow_id or execution_id is None:
            continue
        workflow_id = str(workflow_id)
        try:
            execution_id = int(execution_id)
        except Exception:
            continue
        entry = runtime.setdefault(
            workflow_id,
            {
                "executionId": execution_id,
                "startedAt": None,
                "lastEventAt": None,
                "currentNode": None,
                "source": None,
                "isRunning": False,
            },
        )
        if execution_id >= entry["executionId"]:
            entry["executionId"] = execution_id
            entry["lastEventAt"] = event.get("ts")
            if payload.get("source"):
                entry["source"] = payload.get("source")
            if event.get("eventName") == "n8n.workflow.started":
                entry["startedAt"] = event.get("ts")
                entry["isRunning"] = True
            elif event.get("eventName") in terminal_events:
                entry["isRunning"] = False
            elif payload.get("nodeName"):
                entry["currentNode"] = payload.get("nodeName")
    return runtime


def read_control_state() -> dict:
    if not CONTROL_STATE_PATH.exists():
        return {}
    try:
        return json.loads(CONTROL_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_control_state(payload: dict) -> None:
    CONTROL_STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def summarize_workflow_status(row: dict, progress: dict[int, dict], runtime: dict[str, dict]) -> dict:
    last_execution = row.get("lastExecution") or {}
    execution_id = last_execution.get("id")
    runtime_entry = runtime.get(str(row.get("id")))
    control_state = read_control_state()
    stop_marker = (control_state.get("stoppedAt") or {}).get(row.get("runKey"))
    if runtime_entry and runtime_entry.get("isRunning"):
        row["isRunning"] = True
        execution_id = runtime_entry.get("executionId")
        if not last_execution:
            last_execution = {
                "id": execution_id,
                "status": "running",
                "startedAt": runtime_entry.get("startedAt"),
                "stoppedAt": None,
                "finished": 0,
            }
            row["lastExecution"] = last_execution
    progress_entry = progress.get(int(execution_id)) if execution_id else None
    run_key = row.get("runKey")
    current_node = normalize_step_name(
        run_key, (progress_entry or {}).get("nodeName") or (runtime_entry or {}).get("currentNode")
    )
    steps = WORKFLOW_STEP_HINTS.get(run_key, [])
    current_step_index = None
    last_event_at = (progress_entry or {}).get("lastEventAt") or (runtime_entry or {}).get("lastEventAt")
    upload_readiness = upload_readiness_summary() if run_key == "upload" else None
    if current_node and steps:
        for index, step_name in enumerate(steps, start=1):
            if current_node == step_name:
                current_step_index = index
                break

    if row.get("isRunning"):
        status_key = "running"
        status_label = "Сейчас выполняется"
        status_detail = f"Текущий этап: {current_node}" if current_node else "Запуск уже начался"
        stop_marker_dt = parse_timestamp(stop_marker)
        last_event_dt = parse_timestamp(last_event_at)
        if stop_marker_dt and (not last_event_dt or stop_marker_dt > last_event_dt):
            row["isRunning"] = False
            status_key = "attention"
            status_label = "Остановлен"
            status_detail = "Последний ручной запуск был прерван"
    elif not row.get("active"):
        status_key = "inactive"
        status_label = "Автозапуск выключен"
        status_detail = "Workflow не участвует в расписании"
    elif last_execution.get("status") == "success":
        status_key = "scheduled"
        if upload_readiness:
            status_label = upload_readiness["statusLabel"]
            status_detail = upload_readiness["statusDetail"]
        else:
            status_label = "Ждет следующий запуск"
            status_detail = "Последний запуск завершился успешно"
    elif last_execution.get("status") in {"error", "crashed"}:
        status_key = "attention"
        status_label = "Последний запуск с ошибкой"
        status_detail = "Нужна проверка перед следующим запуском"
    else:
        status_key = "scheduled"
        if upload_readiness:
            status_label = upload_readiness["statusLabel"]
            status_detail = upload_readiness["statusDetail"]
        else:
            status_label = "Готов к запуску"
            status_detail = "Готов к запуску"

    row["statusKey"] = status_key
    row["statusLabel"] = status_label
    row["statusDetail"] = status_detail
    if not row.get("isRunning"):
        current_node = current_node if status_key == "attention" else None
        current_step_index = current_step_index if status_key == "attention" else None
    row["currentNode"] = current_node
    row["currentStepIndex"] = current_step_index
    row["stepTotal"] = len(steps) if steps else None
    row["progressPercent"] = (
        int((current_step_index / len(steps)) * 100) if current_step_index and steps and row.get("isRunning") else None
    )
    row["lastEventAt"] = last_event_at
    row["runSource"] = (progress_entry or {}).get("source") or (runtime_entry or {}).get("source")
    return row


def workflow_error_rows(limit: int = 12) -> list[dict]:
    workflows = {str(item.get("id")): item.get("name") for item in n8n_client.list_workflows()}
    rows = n8n_client.list_executions(status="error", limit=limit)
    return [
        {
            "execution_id": item.get("id"),
            "workflow_id": item.get("workflowId"),
            "status": item.get("status") or "error",
            "startedAt": item.get("startedAt"),
            "stoppedAt": item.get("stoppedAt"),
            "workflow_name": workflows.get(str(item.get("workflowId")), "Unknown workflow"),
            "lastNode": None,
            "description": None,
            "message": item.get("error", {}).get("message") if isinstance(item.get("error"), dict) else None,
            "httpCode": None,
        }
        for item in rows
    ]


def workflow_payload() -> dict:
    statuses = [summarize_workflow_status(item, {}, {}) for item in workflow_status_rows()]
    errors = workflow_error_rows()
    return {
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "workflows": statuses,
        "errors": errors,
        "managedRuns": [{"key": key, "name": value} for key, value in MANAGED_WORKFLOWS.items()],
    }


def health_payload() -> dict:
    databases = database_status()
    try:
        n8n = n8n_client.health()
    except N8NError as exc:
        n8n = {"available": False, "configured": n8n_client.configured, "error": str(exc)}
    return {
        "status": "ok" if all(item["ready"] for item in databases) else "degraded",
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "databases": databases,
        "n8n": n8n,
    }


def parse_client_datetime(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception as exc:
        raise ValueError("Invalid blockedUntil format") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    blocked_until_utc = parsed.astimezone(timezone.utc)
    if blocked_until_utc <= now_utc:
        raise ValueError("blockedUntil must be in the future")
    return blocked_until_utc.isoformat().replace("+00:00", "Z")


def resolve_workflow_id(run_key: str) -> str:
    workflow_id = WORKFLOW_IDS.get(run_key)
    if not workflow_id:
        raise KeyError(run_key)
    return workflow_id


def trigger_workflow(run_key: str) -> None:
    urls = {"render": settings.render_webhook_url, "upload": settings.upload_webhook_url}
    url = urls.get(run_key)
    if not url:
        raise KeyError(run_key)
    if run_key == "upload":
        candidates = upload_candidate_short_names()
        blockers = missing_approvals(MEDIA_DB, candidates) if candidates else []
        if blockers:
            raise ReviewValidationError(json.dumps(blockers, ensure_ascii=False))
    post_webhook(url)
    state = read_control_state()
    stopped = state.get("stoppedAt") or {}
    if run_key in stopped:
        stopped.pop(run_key, None)
        state["stoppedAt"] = stopped
        write_control_state(state)


def stop_workflow(run_key: str) -> None:
    raise N8NError("Stopping executions is not supported by the configured public n8n API", status=501)


def upload_candidate_short_names() -> list[str]:
    conn = db_conn(PUBLISH_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("ATTACH DATABASE ? AS media", (str(MEDIA_DB),))
    rows = cur.execute(
        """
        SELECT DISTINCT sc.short_name
        FROM youtube_accounts account
        JOIN account_active_serials active ON active.account_id = account.id
        JOIN media.shorts_catalog sc ON sc.serial_slug = active.serial_slug
        WHERE account.is_active = 1
          AND account.account_slug <> 'default'
          AND sc.final_path IS NOT NULL
          AND TRIM(sc.final_path) <> ''
          AND NOT EXISTS (
              SELECT 1 FROM youtube_account_cooldowns cooldown
              WHERE cooldown.account_id = account.id
                AND cooldown.is_active = 1
                AND datetime(cooldown.blocked_until) > datetime('now')
          )
          AND NOT EXISTS (
              SELECT 1 FROM youtube_publication_history history
              WHERE history.account_id = account.id
                AND history.short_name = sc.short_name
          )
        ORDER BY sc.short_name
        """
    ).fetchall()
    conn.close()
    return [row["short_name"] for row in rows]


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AutoShortsDashboard/1.0"

    def _send_json(self, payload: dict | list, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        return json.loads(body.decode("utf-8") or "{}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if parsed.path == "/app.css":
            return self._send_file(STATIC_DIR / "app.css", "text/css; charset=utf-8")
        if parsed.path == "/app.js":
            return self._send_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
        if parsed.path == "/api/health":
            return self._send_json(health_payload())
        if parsed.path == "/api/dashboard":
            return self._send_json(dashboard_payload())
        if parsed.path == "/api/serials":
            return self._send_json(media_summary())
        short_review_match = re.fullmatch(r"/api/shorts/([^/]+)/review", parsed.path)
        if short_review_match:
            short_name = unquote(short_review_match.group(1))
            try:
                return self._send_json(get_review(MEDIA_DB, short_name))
            except KeyError:
                return self._send_json({"error": "Short not found"}, 404)
        if parsed.path.startswith("/api/serials/"):
            serial_slug = unquote(parsed.path.split("/api/serials/", 1)[1])
            try:
                return self._send_json(serial_detail(serial_slug))
            except KeyError:
                return self._send_json({"error": "Serial not found"}, 404)
        if parsed.path == "/api/accounts":
            return self._send_json({"accounts": account_rows()})
        if parsed.path == "/api/workflows":
            try:
                return self._send_json(workflow_payload())
            except Exception as exc:
                return self._send_json({"error": f"Workflow monitor is temporarily unavailable: {exc}"}, 503)
        if parsed.path == "/api/youtube-analytics":
            try:
                return self._send_json(youtube_analytics_payload())
            except Exception as exc:
                return self._send_json({"error": f"YouTube analytics are temporarily unavailable: {exc}"}, 503)
        if parsed.path == "/api/n8n/credentials/youtube":
            return self._send_json({"credentials": list_youtube_credentials()})
        if parsed.path == "/api/workflow-errors":
            return self._send_json({"errors": workflow_error_rows()})
        if parsed.path.startswith("/api/accounts/"):
            suffix = parsed.path.split("/api/accounts/", 1)[1]
            if suffix.endswith("/history"):
                account_slug = unquote(suffix[:-8])
                try:
                    return self._send_json(account_history(account_slug))
                except KeyError:
                    return self._send_json({"error": "Account not found"}, 404)
        return self._send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        short_review_match = re.fullmatch(r"/api/shorts/([^/]+)/review", parsed.path)
        if short_review_match:
            short_name = unquote(short_review_match.group(1))
            try:
                review = update_review(MEDIA_DB, short_name, self._read_json())
            except KeyError:
                return self._send_json({"error": "Short not found"}, 404)
            except ReviewValidationError as exc:
                return self._send_json({"error": str(exc)}, 400)
            return self._send_json({"ok": True, "review": review})

        if parsed.path == "/api/serials/create":
            payload = self._read_json()
            raw_name = str(payload.get("name") or "").strip()
            if not raw_name:
                return self._send_json({"error": "name is required"}, 400)
            serial_slug = slugify(raw_name)
            serial_name = titleize(raw_name)
            input_dir = serial_input_dir(serial_slug)
            input_dir.mkdir(parents=True, exist_ok=True)
            return self._send_json(
                {
                    "ok": True,
                    "serial": {
                        "serial_slug": serial_slug,
                        "serial_name": serial_name,
                        "input_dir_name": input_dir.name,
                    },
                    "uploadDefaults": next_episode_defaults(serial_slug, serial_name),
                }
            )

        if parsed.path == "/api/accounts/create":
            payload = self._read_json()
            try:
                result = create_account(payload)
            except ValueError as exc:
                return self._send_json({"error": str(exc)}, 400)
            except KeyError as exc:
                return self._send_json({"error": str(exc)}, 404)
            except Exception as exc:
                return self._send_json({"error": f"Не удалось создать аккаунт: {exc}"}, 500)
            return self._send_json({"ok": True, **result})

        if parsed.path.startswith("/api/serials/") and parsed.path.endswith("/delete"):
            serial_slug = unquote(parsed.path.split("/api/serials/", 1)[1].rsplit("/delete", 1)[0])
            if not serial_slug:
                return self._send_json({"error": "Serial slug is required"}, 400)
            try:
                result = delete_serial(serial_slug)
            except KeyError:
                return self._send_json({"error": "Serial not found"}, 404)
            return self._send_json({"ok": True, **result})

        if parsed.path.startswith("/api/serials/") and parsed.path.endswith("/episodes/upload"):
            serial_slug = unquote(parsed.path.split("/api/serials/", 1)[1].rsplit("/episodes/upload", 1)[0])
            summary = media_summary()
            serial = next((item for item in summary["serials"] if item["serial_slug"] == serial_slug), None)
            if not serial:
                return self._send_json({"error": "Serial not found"}, 404)

            file_name = str(self.headers.get("X-Filename") or "").strip()
            desired_name = str(self.headers.get("X-Desired-Name") or "").strip()
            if not file_name.lower().endswith(".mp4"):
                return self._send_json({"error": "Only .mp4 files are supported"}, 400)

            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return self._send_json({"error": "File body is empty"}, 400)

            file_bytes = self.rfile.read(length)
            if not file_bytes:
                return self._send_json({"error": "Uploaded file is empty"}, 400)

            defaults = next_episode_defaults(serial_slug, serial["serial_name"])
            desired_stem = re.sub(r"[^A-Za-z0-9А-Яа-яЁё _-]+", "", desired_name).strip()
            if not desired_stem:
                desired_stem = defaults["defaultEpisodeStem"]
            final_name = f"{desired_stem}.mp4" if not desired_stem.lower().endswith(".mp4") else desired_stem
            target_dir = serial_input_dir(serial_slug)
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / final_name
            if target_path.exists():
                return self._send_json({"error": f"Episode file already exists: {final_name}"}, 409)

            target_path.write_bytes(file_bytes)
            return self._send_json(
                {
                    "ok": True,
                    "serialSlug": serial_slug,
                    "fileName": final_name,
                    "path": str(target_path),
                    "uploadDefaults": next_episode_defaults(serial_slug, serial["serial_name"]),
                }
            )

        if parsed.path.startswith("/api/accounts/") and parsed.path.endswith("/active-serial"):
            account_slug = unquote(parsed.path.split("/api/accounts/", 1)[1].rsplit("/active-serial", 1)[0])
            payload = self._read_json()
            serial_slug = str(payload.get("serialSlug") or "").strip()
            if not serial_slug:
                return self._send_json({"error": "serialSlug is required"}, 400)
            serial = fetch_one(
                "SELECT serial_slug, serial_name FROM serials WHERE serial_slug = ?", (serial_slug,), db=MEDIA_DB
            )
            account = account_lookup(account_slug)
            if not serial or not account:
                return self._send_json({"error": "Account or serial not found"}, 404)
            execute(
                """
                INSERT INTO account_active_serials (account_id, serial_slug, serial_name, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(account_id) DO UPDATE SET
                    serial_slug = excluded.serial_slug,
                    serial_name = excluded.serial_name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (account["id"], serial["serial_slug"], serial["serial_name"]),
            )
            return self._send_json({"ok": True, "accountSlug": account_slug, "serial": serial})

        if parsed.path.startswith("/api/accounts/") and parsed.path.endswith("/clear-cooldown"):
            account_slug = unquote(parsed.path.split("/api/accounts/", 1)[1].rsplit("/clear-cooldown", 1)[0])
            account = account_lookup(account_slug)
            if not account:
                return self._send_json({"error": "Account not found"}, 404)
            execute(
                """
                UPDATE youtube_account_cooldowns
                SET is_active = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE account_id = ?
                  AND is_active = 1
                """,
                (account["id"],),
            )
            return self._send_json({"ok": True, "accountSlug": account_slug})

        if parsed.path.startswith("/api/accounts/") and parsed.path.endswith("/cooldowns/create"):
            account_slug = unquote(parsed.path.split("/api/accounts/", 1)[1].rsplit("/cooldowns/create", 1)[0])
            payload = self._read_json()
            reason_message = str(payload.get("reasonMessage") or "").strip()
            blocked_until_raw = str(payload.get("blockedUntil") or "").strip()
            if not reason_message:
                return self._send_json({"error": "reasonMessage is required"}, 400)
            if not blocked_until_raw:
                return self._send_json({"error": "blockedUntil is required"}, 400)
            try:
                blocked_until = parse_client_datetime(blocked_until_raw)
            except ValueError as exc:
                return self._send_json({"error": str(exc)}, 400)
            account = account_lookup(account_slug)
            if not account:
                return self._send_json({"error": "Account not found"}, 404)
            conn = db_conn(PUBLISH_DB)
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE youtube_account_cooldowns
                SET is_active = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE account_id = ?
                  AND is_active = 1
                """,
                (account["id"],),
            )
            cur.execute(
                """
                INSERT INTO youtube_account_cooldowns (
                    account_id,
                    reason_code,
                    reason_message,
                    source_workflow_name,
                    source_node_name,
                    blocked_at,
                    blocked_until,
                    is_active,
                    updated_at
                )
                VALUES (?, 'manual_timeout', ?, 'Dashboard', 'Manual Timeout', ?, ?, 1, CURRENT_TIMESTAMP)
                """,
                (
                    account["id"],
                    reason_message,
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    blocked_until,
                ),
            )
            cooldown_id = cur.lastrowid
            conn.commit()
            conn.close()
            return self._send_json({"ok": True, "accountSlug": account_slug, "cooldownId": cooldown_id})

        if parsed.path.startswith("/api/accounts/") and parsed.path.endswith("/cooldowns/delete"):
            account_slug = unquote(parsed.path.split("/api/accounts/", 1)[1].rsplit("/cooldowns/delete", 1)[0])
            payload = self._read_json()
            cooldown_id = payload.get("cooldownId")
            try:
                cooldown_id = int(cooldown_id)
            except Exception:
                return self._send_json({"error": "cooldownId is required"}, 400)
            account = account_lookup(account_slug)
            if not account:
                return self._send_json({"error": "Account not found"}, 404)
            conn = db_conn(PUBLISH_DB)
            cur = conn.cursor()
            cur.execute(
                """
                DELETE FROM youtube_account_cooldowns
                WHERE id = ?
                  AND account_id = ?
                """,
                (cooldown_id, account["id"]),
            )
            deleted = cur.rowcount
            conn.commit()
            conn.close()
            return self._send_json(
                {"ok": True, "accountSlug": account_slug, "deleted": deleted, "cooldownId": cooldown_id}
            )

        if parsed.path.startswith("/api/accounts/") and parsed.path.endswith("/toggle-active"):
            account_slug = unquote(parsed.path.split("/api/accounts/", 1)[1].rsplit("/toggle-active", 1)[0])
            account = account_lookup(account_slug)
            if not account:
                return self._send_json({"error": "Account not found"}, 404)
            new_value = 0 if int(account["is_active"]) else 1
            execute(
                "UPDATE youtube_accounts SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_value, account["id"]),
            )
            return self._send_json({"ok": True, "accountSlug": account_slug, "isActive": new_value})

        if parsed.path.startswith("/api/accounts/") and parsed.path.endswith("/schedule-slots"):
            account_slug = unquote(parsed.path.split("/api/accounts/", 1)[1].rsplit("/schedule-slots", 1)[0])
            payload = self._read_json()
            slots = sorted({str(slot).strip() for slot in (payload.get("slots") or []) if str(slot).strip()})
            account = account_lookup(account_slug)
            if not account:
                return self._send_json({"error": "Account not found"}, 404)
            conn = db_conn(PUBLISH_DB)
            cur = conn.cursor()
            cur.execute("DELETE FROM account_schedule_slots WHERE account_id = ?", (account["id"],))
            for slot in slots:
                cur.execute(
                    """
                    INSERT INTO account_schedule_slots (account_id, slot_time, is_active, updated_at)
                    VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                    """,
                    (account["id"], slot),
                )
            conn.commit()
            conn.close()
            return self._send_json({"ok": True, "accountSlug": account_slug, "slots": slots})

        if parsed.path == "/api/workflows/render/run":
            try:
                trigger_workflow("render")
            except N8NError as exc:
                return self._send_json({"error": f"Не удалось запустить Render Queue: {exc}"}, exc.status or 503)
            except Exception as exc:
                return self._send_json({"error": f"Не удалось запустить Render Queue: {exc}"}, 502)
            return self._send_json({"ok": True, "workflow": "render"})

        if parsed.path == "/api/workflows/upload/run":
            try:
                trigger_workflow("upload")
            except ReviewValidationError as exc:
                try:
                    blockers = json.loads(str(exc))
                except json.JSONDecodeError:
                    blockers = []
                missing = sorted({item for row in blockers for item in row.get("missingApprovals", [])})
                return self._send_json(
                    {
                        "error": "Upload blocked: manual approvals are required",
                        "missingApprovals": missing,
                        "blockedShorts": blockers,
                    },
                    409,
                )
            except N8NError as exc:
                return self._send_json({"error": f"Не удалось запустить Upload: {exc}"}, exc.status or 503)
            except Exception as exc:
                return self._send_json({"error": f"Не удалось запустить Upload: {exc}"}, 502)
            return self._send_json({"ok": True, "workflow": "upload"})

        if parsed.path == "/api/workflows/render/stop":
            return self._send_json({"error": "Остановка недоступна через публичный n8n API"}, 501)

        if parsed.path == "/api/workflows/upload/stop":
            return self._send_json({"error": "Остановка недоступна через публичный n8n API"}, 501)

        if parsed.path == "/api/workflows/statuses/delete":
            return self._send_json({"error": "Deleting workflow status is not supported by the public n8n API"}, 501)

        if parsed.path == "/api/workflows/errors/delete":
            payload = self._read_json()
            if payload.get("all"):
                return self._send_json({"error": "Bulk deletion is not supported by the public n8n API"}, 501)
            execution_ids = payload.get("executionIds") or []
            try:
                deleted = [n8n_client.delete_execution(str(execution_id)) for execution_id in execution_ids]
            except N8NError as exc:
                return self._send_json({"error": str(exc)}, exc.status or 503)
            return self._send_json({"ok": True, "deleted": len(deleted)})

        return self._send_json({"error": "Not found"}, 404)


def main() -> None:
    migrate_all(backup_before=True)
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    print(f"Dashboard listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
