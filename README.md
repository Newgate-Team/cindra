# Cindra

SaaS-платформа для AI-контента в соцсетях. Два раздела:

- **Посты** — тема/бренд-гайд → сгенерированный текст или изображение → публикация по расписанию в подключённые каналы (Telegram, Instagram, Facebook, TikTok), в том числе сразу в несколько аккаунтов.
- **Видео** — студия: сценарий → стиль → производственный бриф (что начитать, что снять или сгенерировать, как смонтировать) → готовый ролик, загруженный пользователем или сгенерированный целиком.

Полная спецификация: [docs/spec.md](docs/spec.md).

Трекер задач: [Jira CIN](https://dalmonded.atlassian.net/jira/software/projects/CIN/boards/100).

## Структура репозитория

```
apps/
  web/     # Frontend — Next.js + TypeScript
  api/     # Backend — FastAPI + Python
           #   app/routers/             — HTTP-эндпоинты
           #   app/content_pipeline/    — промпты, генеративные модели, очередь, модерация
           #   app/social_integrations/ — Telegram, Instagram, Facebook, TikTok
           #   app/billing_integrations/— PayPal
           #   app/scheduler/           — публикация по расписанию (Celery)
           #   migrations/              — Alembic
infra/     # docker-compose (Postgres, Redis)
docs/      # Спецификация, юридические тексты
```

Планировавшиеся `packages/*` из спецификации так и не появились — пайплайн и интеграции остались пакетами внутри `apps/api/app/`.

## Локальная разработка

См. `infra/docker-compose.yml` (Postgres, Redis) и README в `apps/web` / `apps/api`.
