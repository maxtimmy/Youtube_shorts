from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

APPROVAL_DIMENSIONS = ("rights", "policy", "quality")
APPROVAL_STATUSES = {"pending", "approved", "rejected"}


class ReviewValidationError(ValueError):
    pass


def _json_list(value: object, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReviewValidationError(f"{field} must be a list")
    return value


def _parse_json_list(value: str | None) -> list:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _short_row(conn: sqlite3.Connection, short_name: str) -> sqlite3.Row:
    row = conn.execute("SELECT id, short_name FROM shorts WHERE short_name = ?", (short_name,)).fetchone()
    if row is None:
        raise KeyError(short_name)
    return row


def _ensure_approvals(conn: sqlite3.Connection, short_id: int) -> None:
    for dimension in APPROVAL_DIMENSIONS:
        conn.execute(
            "INSERT OR IGNORE INTO short_approvals(short_id, dimension, status) VALUES (?, ?, 'pending')",
            (short_id, dimension),
        )


def get_review(db_path: Path, short_name: str) -> dict:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        short = _short_row(conn, short_name)
        _ensure_approvals(conn, short["id"])
        sources = []
        for row in conn.execute(
            """
            SELECT source.url, source.author, source.platform, source.license,
                   source.checked_at, source.allowed_uses_json, source.notes, link.use_type
            FROM short_sources link
            JOIN content_sources source ON source.id = link.source_id
            WHERE link.short_id = ?
            ORDER BY source.id
            """,
            (short["id"],),
        ):
            item = dict(row)
            item["allowedUses"] = _parse_json_list(item.pop("allowed_uses_json"))
            item["checkedAt"] = item.pop("checked_at")
            item["useType"] = item.pop("use_type")
            sources.append(item)
        creative_row = conn.execute(
            "SELECT script, prompts_json, assets_json, transformations_json, updated_at FROM creative_records WHERE short_id = ?",
            (short["id"],),
        ).fetchone()
        creative = {
            "script": creative_row["script"] if creative_row else "",
            "prompts": _parse_json_list(creative_row["prompts_json"] if creative_row else None),
            "assets": _parse_json_list(creative_row["assets_json"] if creative_row else None),
            "transformations": _parse_json_list(creative_row["transformations_json"] if creative_row else None),
            "updatedAt": creative_row["updated_at"] if creative_row else None,
        }
        approvals = {
            row["dimension"]: {
                "status": row["status"],
                "note": row["note"] or "",
                "reviewedAt": row["reviewed_at"],
            }
            for row in conn.execute(
                "SELECT dimension, status, note, reviewed_at FROM short_approvals WHERE short_id = ?",
                (short["id"],),
            )
        }
        audit = [
            {
                "action": row["action"],
                "details": json.loads(row["details_json"] or "{}"),
                "createdAt": row["created_at"],
            }
            for row in conn.execute(
                "SELECT action, details_json, created_at FROM media_audit_log WHERE short_id = ? ORDER BY id DESC LIMIT 30",
                (short["id"],),
            )
        ]
        conn.commit()
    missing = [dimension for dimension in APPROVAL_DIMENSIONS if approvals[dimension]["status"] != "approved"]
    return {
        "shortName": short_name,
        "sources": sources,
        "creative": creative,
        "approvals": approvals,
        "publicationReady": not missing,
        "missingApprovals": missing,
        "audit": audit,
    }


def _validate_source(source: object) -> dict:
    if not isinstance(source, dict):
        raise ReviewValidationError("Each source must be an object")
    url = str(source.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ReviewValidationError("Source URL must use http or https")
    return {
        "url": url,
        "author": str(source.get("author") or "").strip(),
        "platform": str(source.get("platform") or "").strip(),
        "license": str(source.get("license") or "").strip(),
        "checked_at": str(source.get("checkedAt") or "").strip() or None,
        "allowed_uses": _json_list(source.get("allowedUses"), "allowedUses"),
        "notes": str(source.get("notes") or "").strip(),
        "use_type": str(source.get("useType") or "reference").strip() or "reference",
    }


def update_review(db_path: Path, short_name: str, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ReviewValidationError("JSON object is required")
    sources = [_validate_source(item) for item in _json_list(payload.get("sources"), "sources")]
    creative = payload.get("creative") or {}
    if not isinstance(creative, dict):
        raise ReviewValidationError("creative must be an object")
    approvals = payload.get("approvals") or {}
    if not isinstance(approvals, dict):
        raise ReviewValidationError("approvals must be an object")
    unexpected = set(approvals) - set(APPROVAL_DIMENSIONS)
    if unexpected:
        raise ReviewValidationError(f"Unsupported approval dimensions: {', '.join(sorted(unexpected))}")

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        short = _short_row(conn, short_name)
        _ensure_approvals(conn, short["id"])

        conn.execute("DELETE FROM short_sources WHERE short_id = ?", (short["id"],))
        for source in sources:
            conn.execute(
                """
                INSERT INTO content_sources(url, author, platform, license, checked_at, allowed_uses_json, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(url) DO UPDATE SET
                    author = excluded.author, platform = excluded.platform, license = excluded.license,
                    checked_at = excluded.checked_at, allowed_uses_json = excluded.allowed_uses_json,
                    notes = excluded.notes, updated_at = CURRENT_TIMESTAMP
                """,
                (
                    source["url"],
                    source["author"],
                    source["platform"],
                    source["license"],
                    source["checked_at"],
                    json.dumps(source["allowed_uses"], ensure_ascii=False),
                    source["notes"],
                ),
            )
            source_id = conn.execute("SELECT id FROM content_sources WHERE url = ?", (source["url"],)).fetchone()[0]
            conn.execute(
                "INSERT INTO short_sources(short_id, source_id, use_type) VALUES (?, ?, ?)",
                (short["id"], source_id, source["use_type"]),
            )

        prompts = _json_list(creative.get("prompts"), "prompts")
        assets = _json_list(creative.get("assets"), "assets")
        transformations = _json_list(creative.get("transformations"), "transformations")
        conn.execute(
            """
            INSERT INTO creative_records(short_id, script, prompts_json, assets_json, transformations_json, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(short_id) DO UPDATE SET
                script = excluded.script, prompts_json = excluded.prompts_json,
                assets_json = excluded.assets_json, transformations_json = excluded.transformations_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                short["id"],
                str(creative.get("script") or ""),
                json.dumps(prompts, ensure_ascii=False),
                json.dumps(assets, ensure_ascii=False),
                json.dumps(transformations, ensure_ascii=False),
            ),
        )

        approval_changes: dict[str, str] = {}
        for dimension, decision in approvals.items():
            if not isinstance(decision, dict):
                raise ReviewValidationError(f"Approval {dimension} must be an object")
            status = str(decision.get("status") or "pending").strip()
            if status not in APPROVAL_STATUSES:
                raise ReviewValidationError(f"Invalid approval status for {dimension}")
            note = str(decision.get("note") or "").strip()
            reviewed_at = now if status != "pending" else None
            conn.execute(
                """
                UPDATE short_approvals
                SET status = ?, note = ?, reviewed_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE short_id = ? AND dimension = ?
                """,
                (status, note, reviewed_at, short["id"], dimension),
            )
            approval_changes[dimension] = status

        conn.execute(
            "INSERT INTO media_audit_log(short_id, short_name, action, details_json) VALUES (?, ?, 'review.updated', ?)",
            (
                short["id"],
                short_name,
                json.dumps(
                    {"sourceCount": len(sources), "creativeUpdated": True, "approvals": approval_changes},
                    ensure_ascii=False,
                ),
            ),
        )
        conn.commit()
    return get_review(db_path, short_name)


def missing_approvals(db_path: Path, short_names: list[str] | None = None) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        params: tuple = ()
        where = ""
        if short_names:
            placeholders = ",".join("?" for _ in short_names)
            where = f"WHERE short_name IN ({placeholders})"
            params = tuple(short_names)
        shorts = conn.execute(f"SELECT id, short_name FROM shorts {where} ORDER BY short_name", params).fetchall()
        result = []
        for short in shorts:
            _ensure_approvals(conn, short["id"])
            statuses = {
                row["dimension"]: row["status"]
                for row in conn.execute(
                    "SELECT dimension, status FROM short_approvals WHERE short_id = ?", (short["id"],)
                )
            }
            missing = [dimension for dimension in APPROVAL_DIMENSIONS if statuses.get(dimension) != "approved"]
            if missing:
                result.append({"shortName": short["short_name"], "missingApprovals": missing})
        conn.commit()
    return result
