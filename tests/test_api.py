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
    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(youtube_credentials=lambda: [{"id": "credential-id", "name": "Demo credential"}]),
    )
    server.WORKFLOW_IDS["upload"] = "upload-id"
    database.migrate_all(backup_before=False)
    with database.connect(media) as conn:
        conn.execute("INSERT INTO serials(serial_slug, serial_name) VALUES ('demo', 'Demo')")

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
