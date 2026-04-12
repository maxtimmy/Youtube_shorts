# Auto Shorts Control Center

Локальный стек для пайплайна шортсов на базе `n8n` и отдельного dashboard UI.

## Что внутри

- `n8n` для render/upload workflow
- `dashboard` на `http://localhost:8787` для управления сериалами, аккаунтами, cooldown и публикациями
- `SQLite` базы для:
  - media catalog
  - publishing / YouTube accounts
  - internal `n8n` state

## Основные директории

- `dashboard/` — веб-интерфейс и backend панели
- `banners/` — баннеры для рендера
- `input/` — исходные видео по сериалам
- `output/` — результаты рендера, catalog DB, publishing DB
- `temp/` — служебные скрипты и runtime helper-файлы

## Сервисы

- `n8n`: [http://localhost:5678](http://localhost:5678)
- `dashboard`: [http://localhost:8787](http://localhost:8787)

## Что умеет dashboard

- просмотр сериалов, эпизодов и шортсов
- создание и удаление сериалов
- загрузка новых эпизодов
- управление YouTube-аккаунтами
- выбор активного сериала
- настройка слотов публикации
- ручные cooldown / timeout
- календарь публикаций
- мониторинг `n8n`
- ручной запуск `Render Queue` и `YouTube Upload`

## Важные базы

- `output/media-library.sqlite`
- `output/youtube-publishing.sqlite`

## Локальный запуск

```powershell
docker compose up -d
```

Если нужен только dashboard:

```powershell
docker compose up -d dashboard
```

## Примечания

- Локальные данные, видео, базы и временные файлы исключены из git через `.gitignore`.
- Для добавления нового YouTube-аккаунта нужен заранее созданный YouTube credential в `n8n`.
