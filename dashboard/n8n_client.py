from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from config import settings


class N8NError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class N8NClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10, opener=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _request(self, method: str, path: str, payload: dict | None = None, query: dict | None = None) -> dict:
        if not self.configured:
            raise N8NError("n8n API is not configured")
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            method=method,
            data=body,
            headers={"X-N8N-API-KEY": self.api_key, "Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise N8NError(f"n8n API returned HTTP {exc.code}: {detail}", status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise N8NError(f"n8n API is unavailable: {exc}") from exc
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise N8NError("n8n API returned malformed JSON") from exc
        if not isinstance(value, dict):
            raise N8NError("n8n API returned an unexpected response")
        return value

    def health(self) -> dict:
        workflows = self.list_workflows(limit=1)
        return {"available": True, "configured": True, "workflowCountSample": len(workflows)}

    def list_workflows(self, limit: int = 100) -> list[dict]:
        payload = self._request("GET", "workflows", query={"limit": limit})
        rows = payload.get("data", payload.get("workflows", []))
        if not isinstance(rows, list):
            raise N8NError("n8n workflows response has no list")
        return [row for row in rows if isinstance(row, dict)]

    def get_workflow(self, workflow_id: str) -> dict:
        return self._request("GET", f"workflows/{urllib.parse.quote(workflow_id)}")

    def update_workflow(self, workflow_id: str, workflow: dict) -> dict:
        allowed = {key: workflow[key] for key in ("name", "nodes", "connections", "settings") if key in workflow}
        return self._request("PUT", f"workflows/{urllib.parse.quote(workflow_id)}", allowed)

    def list_executions(
        self, workflow_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict]:
        query = {"limit": limit, "includeData": "false"}
        if workflow_id:
            query["workflowId"] = workflow_id
        if status:
            query["status"] = status
        payload = self._request("GET", "executions", query=query)
        rows = payload.get("data", payload.get("executions", []))
        if not isinstance(rows, list):
            raise N8NError("n8n executions response has no list")
        return [row for row in rows if isinstance(row, dict)]

    def delete_execution(self, execution_id: str) -> dict:
        return self._request("DELETE", f"executions/{urllib.parse.quote(str(execution_id))}")


def post_webhook(url: str, timeout: float = 20) -> dict:
    if not url:
        raise N8NError("Workflow webhook is not configured")
    request = urllib.request.Request(url, method="POST", headers={"Content-Type": "application/json"}, data=b"{}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise N8NError(f"Workflow webhook returned HTTP {exc.code}: {detail}", status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise N8NError(f"Workflow webhook is unavailable: {exc}") from exc
    if not raw:
        return {"ok": True}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": True, "response": raw[:500]}
    return payload if isinstance(payload, dict) else {"ok": True, "response": payload}


def sanitize_workflow(workflow: dict) -> dict:
    result = {key: workflow.get(key) for key in ("id", "name", "active", "nodes", "connections", "settings")}
    for node in result.get("nodes") or []:
        if isinstance(node, dict):
            node.pop("credentials", None)
            parameters = node.get("parameters")
            if isinstance(parameters, dict):
                for key in list(parameters):
                    lowered = key.lower()
                    if any(token in lowered for token in ("token", "secret", "password", "apikey", "api_key")):
                        parameters[key] = "REDACTED"
    return result


def export_workflows(client: N8NClient, destination: Path) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    exported = []
    for workflow in client.list_workflows():
        workflow_id = str(workflow.get("id") or "")
        if not workflow_id:
            continue
        full = client.get_workflow(workflow_id)
        safe = sanitize_workflow(full)
        slug = "".join(char.lower() if char.isalnum() else "-" for char in str(safe.get("name") or workflow_id))
        slug = "-".join(filter(None, slug.split("-"))) or workflow_id
        target = destination / f"{slug}.json"
        target.write_text(json.dumps(safe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        exported.append(str(target))
    return exported


client = N8NClient(settings.n8n_api_url, settings.n8n_api_key)
