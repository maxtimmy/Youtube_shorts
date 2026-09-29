import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import database
import server


class FakeN8N:
    configured = True

    def health(self):
        return {"available": True, "configured": True}

    def get_workflow(self, workflow_id):
        return {
            "id": workflow_id,
            "name": "Auto Shorts - YouTube Upload",
            "nodes": [
                {"name": "Schedule Trigger"},
                {"name": "Manual Webhook - Upload"},
                {"name": "Load Next Short"},
                {"name": "Generate YouTube Metadata"},
                {"name": "Record Upload In DB"},
                {"name": "Read Short File - Template"},
                {"name": "Upload to YouTube - Template"},
                {"name": "Merge Upload Result - Template"},
            ],
            "settings": {},
        }

    def update_workflow(self, workflow_id, workflow):
        return {"id": workflow_id, "versionId": "test-version"}

    def list_workflows(self, limit=100):
        return []

    def list_executions(self, **kwargs):
        return []


def request(base_url, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{base_url}{path}",
        body,
        method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_api_smoke_on_temporary_databases(tmp_path, monkeypatch):
    migrations = Path(database.__file__).parent / "migrations"
    media = tmp_path / "media.sqlite"
    publishing = tmp_path / "publishing.sqlite"
    monkeypatch.setattr(
        database,
        "DATABASES",
        {"media": (media, migrations / "media"), "publishing": (publishing, migrations / "publishing")},
    )
    monkeypatch.setattr(server, "MEDIA_DB", media)
    monkeypatch.setattr(server, "PUBLISH_DB", publishing)
    monkeypatch.setattr(server, "INPUT_ROOT", tmp_path / "input")
    monkeypatch.setattr(server, "n8n_client", FakeN8N())
    monkeypatch.setattr(server, "post_webhook", lambda url: {"ok": True})
    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            youtube_credentials=lambda: [{"id": "credential-id", "name": "Demo credential"}],
            render_webhook_url="http://n8n/render",
            upload_webhook_url="http://n8n/upload",
        ),
    )
    server.WORKFLOW_IDS["upload"] = "upload-id"
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

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, health = request(base_url, "/api/health")
        assert status == 200 and health["status"] == "ok"
        status, serials = request(base_url, "/api/serials")
        assert status == 200 and serials["serials"][0]["serial_slug"] == "demo"
        status, created = request(
            base_url,
            "/api/accounts/create",
            {"accountName": "Demo channel", "credentialId": "credential-id", "serialSlug": "demo", "slots": ["12:00"]},
        )
        assert status == 200, created
        assert created["account"]["account_slug"] == "demo-channel"
        status, blocked = request(base_url, "/api/workflows/upload/run", {})
        assert status == 409, blocked
        assert blocked["missingApprovals"] == ["policy", "quality", "rights"]

        status, pending_review = request(base_url, "/api/shorts/Demo01_part01/review")
        assert status == 200 and pending_review["publicationReady"] is False
        status, saved = request(
            base_url,
            "/api/shorts/Demo01_part01/review",
            {
                "sources": [{"url": "https://example.com/reference", "allowedUses": ["inspiration"]}],
                "creative": {"script": "Original script"},
                "approvals": {
                    "rights": {"status": "approved", "note": "checked"},
                    "policy": {"status": "approved", "note": "checked"},
                    "quality": {"status": "approved", "note": "checked"},
                },
            },
        )
        assert status == 200 and saved["review"]["publicationReady"] is True
        status, started = request(base_url, "/api/workflows/upload/run", {})
        assert status == 200 and started["workflow"] == "upload"
        status, slots = request(base_url, "/api/accounts/demo-channel/schedule-slots", {"slots": ["13:00"]})
        assert status == 200 and slots["slots"] == ["13:00"]

        class DownN8N:
            configured = True

            def list_workflows(self, *args, **kwargs):
                raise server.N8NError("offline")

        monkeypatch.setattr(server, "n8n_client", DownN8N())
        status, unavailable = request(base_url, "/api/workflows")
        assert status == 503 and "offline" in unavailable["error"]
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def test_health_survives_unavailable_n8n(monkeypatch):
    class Down:
        configured = True

        def health(self):
            raise server.N8NError("offline")

    monkeypatch.setattr(server, "n8n_client", Down())
    monkeypatch.setattr(server, "database_status", lambda: [{"ready": True}])

    payload = server.health_payload()

    assert payload["status"] == "ok"
    assert payload["n8n"]["available"] is False
