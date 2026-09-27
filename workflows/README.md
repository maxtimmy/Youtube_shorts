# n8n workflow exports

This directory contains sanitized exports created with:

```bash
python dashboard/cli.py workflows export --destination workflows
```

The exporter removes credential bindings and common secret-like node parameters. Review every diff before committing it. Execution history, credential records and n8n's internal database must never be copied here.

No production workflow was available in the repository when this directory was introduced, so no fabricated workflow is included.
