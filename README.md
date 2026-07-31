# Cindra

SaaS-платформа для AI-контента в соцсетях: пользователь задаёт тему/бренд-гайд, система генерирует тексты, изображения и видео, затем публикует их по расписанию в подключённые каналы.

Полная спецификация: [docs/spec.md](docs/spec.md).

Трекер задач: [Jira CIN](https://dalmonded.atlassian.net/jira/software/projects/CIN/boards/100).

## Структура репозитория

```
apps/
  web/     # Frontend — Next.js + TypeScript
  api/     # Backend — FastAPI + Python
packages/
  content-pipeline/     # Промпты, вызовы LLM/генеративных моделей, очередь задач
  social-integrations/  # Адаптеры публикации (Telegram, Instagram)
  shared-types/         # OpenAPI-контракт между web и api
infra/     # docker-compose, деплой-конфиги, миграции БД
docs/      # Спецификация, ADR
```

## Локальная разработка

См. `infra/docker-compose.yml` (Postgres, Redis) и README в `apps/web` / `apps/api`.
