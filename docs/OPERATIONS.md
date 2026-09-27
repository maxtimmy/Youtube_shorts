# Operations

## Первый запуск

1. Скопируйте `.env.example` в `.env` и замените `N8N_ENCRYPTION_KEY`.
2. Запустите `python dashboard/cli.py db init`.
3. Запустите `docker compose up -d --build`.
4. Проверьте `http://localhost:8787/api/health`.

Dashboard автоматически применяет ожидающие миграции при старте. Если база уже существует, перед изменением создаётся SQLite backup в `backups/`.

## Обновление

```bash
python dashboard/cli.py backup create --label before-update
python dashboard/cli.py db migrate
docker compose up -d --build
```

После обновления проверьте, что обе базы имеют `ready: true`, а отсутствие n8n отражается только в поле `n8n.available`.

## Восстановление

Восстановление намеренно не перезаписывает рабочие файлы. Сначала восстановите backup в новый каталог, проверьте контрольные суммы и только затем вручную замените базы при остановленных сервисах.

```bash
python dashboard/cli.py backup restore backups/<backup-id> restored-data
```

## n8n

Dashboard использует `X-N8N-API-KEY` для workflow/execution API и отдельные webhook URL для запуска. Он не читает `database.sqlite`, не монтирует n8n volume и не получает расшифрованные credentials.

Публичный API используемой инсталляции может не поддерживать остановку активного execution. В этом случае кнопка остановки скрыта. Не возвращайте Docker socket в контейнер dashboard как обходной путь.
