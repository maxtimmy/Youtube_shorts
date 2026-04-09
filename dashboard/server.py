import json
import re
import sqlite3
import urllib.request
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path("/app")
STATIC_DIR = ROOT / "static"
MEDIA_DB = Path("/work/output/media-library.sqlite")
PUBLISH_DB = Path("/work/output/youtube-publishing.sqlite")
N8N_DB = Path("/work/data/database.sqlite")
HOST = "0.0.0.0"
PORT = 8787
N8N_CONTAINER = "n8n_local"
MANAGED_WORKFLOWS = {
    "render": "Auto Shorts - Render Queue",
    "upload": "Auto Shorts - YouTube Upload",
}
MANUAL_WEBHOOKS = {
    "render": "manual-render-queue",
    "upload": "manual-youtube-upload",
}


def db_conn(path: Path) -> sqlite3.Connection:
    if path == N8N_DB:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_json(sql: str, params: tuple = (), *, db: Path = PUBLISH_DB) -> list[dict]:
    conn = db_conn(db)
    cur = conn.cursor()
    rows = [dict(row) for row in cur.execute(sql, params).fetchall()]
    conn.close()
    return rows


def fetch_one(sql: str, params: tuple = (), *, db: Path = PUBLISH_DB) -> dict | None:
    conn = db_conn(db)
    cur = conn.cursor()
    row = cur.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None


def execute(sql: str, params: tuple = (), *, db: Path = PUBLISH_DB) -> None:
    conn = db_conn(db)
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


def media_summary() -> dict:
    conn = db_conn(MEDIA_DB)
    cur = conn.cursor()
    serials = [dict(row) for row in cur.execute(
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
    ).fetchall()]
    overview = {
        "serialCount": cur.execute("SELECT COUNT(*) AS c FROM serials").fetchone()["c"],
        "episodeCount": cur.execute("SELECT COUNT(*) AS c FROM episodes").fetchone()["c"],
        "shortCount": cur.execute("SELECT COUNT(*) AS c FROM shorts").fetchone()["c"],
        "serials": serials,
    }
    conn.close()
    return overview


def serial_detail(serial_slug: str) -> dict:
    conn = db_conn(MEDIA_DB)
    cur = conn.cursor()
    serial = cur.execute(
        "SELECT serial_slug, serial_name FROM serials WHERE serial_slug = ?",
        (serial_slug,),
    ).fetchone()
    if not serial:
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
        episode["shorts"] = [dict(short_row) for short_row in cur.execute(
            """
            SELECT
                sh.short_name,
                sh.short_part,
                sh.duration,
                sh.final_path,
                sh.clip_path,
                sh.score,
                sh.text
            FROM shorts sh
            JOIN episodes e ON e.id = sh.episode_id
            JOIN serials s ON s.id = e.serial_id
            WHERE s.serial_slug = ?
              AND e.episode_base_name = ?
            ORDER BY sh.short_part
            """,
            (serial_slug, row["episode_base_name"]),
        ).fetchall()]
        episodes.append(episode)

    conn.close()
    return {"serial": dict(serial), "episodes": episodes}


def account_rows() -> list[dict]:
    conn = db_conn(PUBLISH_DB)
    cur = conn.cursor()
    rows = [dict(row) for row in cur.execute(
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
            (SELECT COUNT(*) FROM youtube_publication_history h WHERE h.account_id = a.id AND h.status = 'scheduled') AS scheduled_total,
            (SELECT COUNT(*) FROM youtube_publication_history h WHERE h.account_id = a.id AND h.status = 'published') AS published_total
        FROM youtube_accounts a
        LEFT JOIN account_active_serials s ON s.account_id = a.id
        LEFT JOIN youtube_account_cooldowns c
          ON c.account_id = a.id
         AND c.is_active = 1
         AND datetime(c.blocked_until) > datetime('now')
        WHERE a.account_slug <> 'default'
        ORDER BY a.is_active DESC, a.account_name
        """
    ).fetchall()]
    conn.close()
    return rows


def account_history(account_slug: str) -> dict:
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
        WHERE a.account_slug = ?
          AND a.account_slug <> 'default'
        """,
        (account_slug,),
    )
    if not account:
        raise KeyError(account_slug)

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
            status
        FROM youtube_publication_history
        WHERE account_id = ?
        ORDER BY datetime(uploaded_at) DESC, id DESC
        LIMIT 200
        """,
        (account["id"],),
    )
    cooldowns = fetch_json(
        """
        SELECT
            reason_code,
            reason_message,
            source_workflow_name,
            source_node_name,
            blocked_at,
            blocked_until,
            is_active
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
            h.status,
            h.uploaded_at,
            h.publish_at_local
        FROM youtube_publication_history h
        JOIN youtube_accounts a ON a.id = h.account_id
        WHERE a.account_slug <> 'default'
        ORDER BY datetime(h.uploaded_at) DESC, h.id DESC
        LIMIT 20
        """
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
        "activeCooldowns": active_cooldowns,
    }


def extract_error_details(raw_data: str | None) -> dict:
    text = raw_data or ""
    try:
        compact = json.loads(text)
        if isinstance(compact, list) and compact:
            def resolve(value):
                if isinstance(value, str) and value.isdigit():
                    index = int(value)
                    if 0 <= index < len(compact):
                        return resolve(compact[index])
                if isinstance(value, list):
                    return [resolve(item) for item in value]
                if isinstance(value, dict):
                    return {key: resolve(item) for key, item in value.items()}
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
        return value.replace("\\n", "\n").replace("\\\"", "\"")

    return {
        "lastNode": match(r'"lastNodeExecuted":"([^"]+)"'),
        "description": match(r'"description":"([^"]+)"'),
        "message": match(r'"message":"([^"]+)"'),
        "httpCode": match(r'"httpCode":"([^"]+)"'),
    }


def workflow_status_rows() -> list[dict]:
    workflows = fetch_all(
        """
        SELECT id, name, active, updatedAt
        FROM workflow_entity
        WHERE isArchived = 0
        ORDER BY name
        """,
        db=N8N_DB,
    )
    conn = db_conn(N8N_DB)
    cur = conn.cursor()
    rows: list[dict] = []
    for workflow in workflows:
        last_execution = cur.execute(
            """
            SELECT id, status, startedAt, stoppedAt, finished
            FROM execution_entity
            WHERE workflowId = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (workflow["id"],),
        ).fetchone()
        running_count = cur.execute(
            """
            SELECT COUNT(*) AS c
            FROM execution_entity
            WHERE workflowId = ?
              AND status = 'running'
              AND finished = 0
            """,
            (workflow["id"],),
        ).fetchone()["c"]
        row = dict(workflow)
        row["active"] = int(row["active"])
        row["runningCount"] = int(running_count)
        row["isRunning"] = int(running_count) > 0
        row["lastExecution"] = dict(last_execution) if last_execution else None
        row["canRunManually"] = workflow["name"] in MANAGED_WORKFLOWS.values()
        row["runKey"] = next((key for key, value in MANAGED_WORKFLOWS.items() if value == workflow["name"]), None)
        rows.append(row)
    conn.close()
    return rows


def workflow_error_rows(limit: int = 12) -> list[dict]:
    conn = db_conn(N8N_DB)
    cur = conn.cursor()
    rows = []
    for row in cur.execute(
        """
        SELECT
            e.id AS execution_id,
            e.workflowId AS workflow_id,
            e.status,
            e.startedAt,
            e.stoppedAt,
            w.name AS workflow_name,
            d.data AS raw_data
        FROM execution_entity e
        JOIN workflow_entity w ON w.id = e.workflowId
        LEFT JOIN execution_data d ON d.executionId = e.id
        WHERE e.status = 'error'
        ORDER BY e.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall():
        item = dict(row)
        details = extract_error_details(item.pop("raw_data", None))
        item.update(details)
        rows.append(item)
    conn.close()
    return rows


def workflow_payload() -> dict:
    statuses = workflow_status_rows()
    errors = workflow_error_rows()
    return {
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "workflows": statuses,
        "errors": errors,
        "managedRuns": [{"key": key, "name": value} for key, value in MANAGED_WORKFLOWS.items()],
    }


def resolve_workflow_id(run_key: str) -> str:
    workflow_name = MANAGED_WORKFLOWS.get(run_key)
    if not workflow_name:
        raise KeyError(run_key)
    row = fetch_one("SELECT id FROM workflow_entity WHERE name = ? AND isArchived = 0", (workflow_name,), db=N8N_DB)
    if not row:
        raise KeyError(run_key)
    return str(row["id"])


def trigger_workflow(run_key: str) -> None:
    path = MANUAL_WEBHOOKS.get(run_key)
    if not path:
        raise KeyError(run_key)
    request = urllib.request.Request(
        f"http://n8n_local:5678/webhook/{path}",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=b"{}",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


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
        if parsed.path == "/api/dashboard":
            return self._send_json(dashboard_payload())
        if parsed.path == "/api/serials":
            return self._send_json(media_summary())
        if parsed.path.startswith("/api/serials/"):
            serial_slug = parsed.path.split("/api/serials/", 1)[1]
            try:
                return self._send_json(serial_detail(serial_slug))
            except KeyError:
                return self._send_json({"error": "Serial not found"}, 404)
        if parsed.path == "/api/accounts":
            return self._send_json({"accounts": account_rows()})
        if parsed.path == "/api/workflows":
            return self._send_json(workflow_payload())
        if parsed.path == "/api/workflow-errors":
            return self._send_json({"errors": workflow_error_rows()})
        if parsed.path.startswith("/api/accounts/"):
            suffix = parsed.path.split("/api/accounts/", 1)[1]
            if suffix.endswith("/history"):
                account_slug = suffix[:-8]
                try:
                    return self._send_json(account_history(account_slug))
                except KeyError:
                    return self._send_json({"error": "Account not found"}, 404)
        return self._send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/accounts/") and parsed.path.endswith("/active-serial"):
            account_slug = parsed.path.split("/api/accounts/", 1)[1].rsplit("/active-serial", 1)[0]
            payload = self._read_json()
            serial_slug = str(payload.get("serialSlug") or "").strip()
            if not serial_slug:
                return self._send_json({"error": "serialSlug is required"}, 400)
            serial = fetch_one("SELECT serial_slug, serial_name FROM serials WHERE serial_slug = ?", (serial_slug,), db=MEDIA_DB)
            account = fetch_one("SELECT id FROM youtube_accounts WHERE account_slug = ? AND account_slug <> 'default'", (account_slug,))
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
            account_slug = parsed.path.split("/api/accounts/", 1)[1].rsplit("/clear-cooldown", 1)[0]
            account = fetch_one("SELECT id FROM youtube_accounts WHERE account_slug = ? AND account_slug <> 'default'", (account_slug,))
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

        if parsed.path.startswith("/api/accounts/") and parsed.path.endswith("/toggle-active"):
            account_slug = parsed.path.split("/api/accounts/", 1)[1].rsplit("/toggle-active", 1)[0]
            account = fetch_one("SELECT id, is_active FROM youtube_accounts WHERE account_slug = ? AND account_slug <> 'default'", (account_slug,))
            if not account:
                return self._send_json({"error": "Account not found"}, 404)
            new_value = 0 if int(account["is_active"]) else 1
            execute("UPDATE youtube_accounts SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_value, account["id"]))
            return self._send_json({"ok": True, "accountSlug": account_slug, "isActive": new_value})

        if parsed.path.startswith("/api/accounts/") and parsed.path.endswith("/schedule-slots"):
            account_slug = parsed.path.split("/api/accounts/", 1)[1].rsplit("/schedule-slots", 1)[0]
            payload = self._read_json()
            slots = sorted({str(slot).strip() for slot in (payload.get("slots") or []) if str(slot).strip()})
            account = fetch_one("SELECT id FROM youtube_accounts WHERE account_slug = ? AND account_slug <> 'default'", (account_slug,))
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
            trigger_workflow("render")
            return self._send_json({"ok": True, "workflow": "render"})

        if parsed.path == "/api/workflows/upload/run":
            trigger_workflow("upload")
            return self._send_json({"ok": True, "workflow": "upload"})

        return self._send_json({"error": "Not found"}, 404)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    print(f"Dashboard listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
