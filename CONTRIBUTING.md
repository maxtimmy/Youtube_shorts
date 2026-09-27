# Участие в разработке

## Перед изменениями

1. Создайте issue с ожидаемым результатом и критериями приёмки.
2. Для функций, связанных с источниками или публикацией, опишите происхождение материалов, лицензию и ручную контрольную точку.
3. Не добавляйте реальные токены, cookies, OAuth-файлы, базы и медиа.

## Локальная работа

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pre-commit install
```

Перед pull request:

```bash
pre-commit run --all-files
python -m compileall -q dashboard
```

Если менялась Docker-конфигурация, дополнительно выполните:

```bash
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
```

## Pull request

- Делайте одно логическое изменение в одном PR.
- Укажите, что изменено, как проверено и какие риски остаются.
- Для UI приложите скриншот.
- Для контентного пайплайна заполните checklist прав из `docs/PIPELINE.md`.
- Не включайте автоматическую публикацию по умолчанию: сначала должен быть явный ручной approval.
