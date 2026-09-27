from __future__ import annotations

import argparse
import json
from pathlib import Path

from config import settings
from database import create_backup, database_status, migrate_all, restore_backup
from fixtures import load_fixtures
from n8n_client import client, export_workflows


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="shorts-admin")
    groups = root.add_subparsers(dest="group", required=True)

    db = groups.add_parser("db")
    db_commands = db.add_subparsers(dest="command", required=True)
    db_commands.add_parser("init")
    db_commands.add_parser("migrate")
    db_commands.add_parser("status")
    fixtures = db_commands.add_parser("fixtures")
    fixtures.add_argument("--force", action="store_true")

    backup = groups.add_parser("backup")
    backup_commands = backup.add_subparsers(dest="command", required=True)
    create = backup_commands.add_parser("create")
    create.add_argument("--label")
    restore = backup_commands.add_parser("restore")
    restore.add_argument("backup_dir", type=Path)
    restore.add_argument("destination", type=Path)

    workflows = groups.add_parser("workflows")
    workflow_commands = workflows.add_subparsers(dest="command", required=True)
    export = workflow_commands.add_parser("export")
    export.add_argument("--destination", type=Path, default=settings.workflow_export_root)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.group == "db" and args.command in {"init", "migrate"}:
        result = migrate_all(backup_before=True)
    elif args.group == "db" and args.command == "status":
        result = database_status()
    elif args.group == "db" and args.command == "fixtures":
        result = load_fixtures(force=args.force)
    elif args.group == "backup" and args.command == "create":
        result = create_backup(label=args.label)
    elif args.group == "backup" and args.command == "restore":
        result = restore_backup(args.backup_dir, args.destination)
    elif args.group == "workflows" and args.command == "export":
        result = {"exported": export_workflows(client, args.destination)}
    else:
        raise AssertionError("Unhandled command")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
