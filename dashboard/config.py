from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default)).expanduser()


@dataclass(frozen=True)
class Settings:
    app_root: Path = _path("APP_ROOT", "/app")
    media_db: Path = _path("MEDIA_DB_PATH", "/work/output/media-library.sqlite")
    publish_db: Path = _path("PUBLISH_DB_PATH", "/work/output/youtube-publishing.sqlite")
    input_root: Path = _path("INPUT_ROOT", "/work/input")
    backup_root: Path = _path("BACKUP_ROOT", "/work/backups")
    workflow_export_root: Path = _path("WORKFLOW_EXPORT_ROOT", "/work/workflows")
    n8n_api_url: str = os.getenv("N8N_API_URL", "http://n8n:5678/api/v1").rstrip("/")
    n8n_api_key: str = os.getenv("N8N_API_KEY", "")
    render_webhook_url: str = os.getenv("N8N_RENDER_WEBHOOK_URL", "http://n8n:5678/webhook/manual-render-queue")
    upload_webhook_url: str = os.getenv("N8N_UPLOAD_WEBHOOK_URL", "http://n8n:5678/webhook/manual-youtube-upload")
    render_workflow_id: str = os.getenv("N8N_RENDER_WORKFLOW_ID", "")
    upload_workflow_id: str = os.getenv("N8N_UPLOAD_WORKFLOW_ID", "")
    host: str = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port: int = int(os.getenv("DASHBOARD_PORT", "8787"))

    def youtube_credentials(self) -> list[dict]:
        raw = os.getenv("YOUTUBE_CREDENTIALS_JSON", "[]")
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(values, list):
            return []
        return [item for item in values if isinstance(item, dict) and item.get("id") and item.get("name")]


settings = Settings()
