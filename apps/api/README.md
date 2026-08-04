# apps/api

Backend Cindra: FastAPI + Python.

## Разработка

```bash
cp .env.example .env
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

`.env` — gitignored, только для локальных значений. Продакшн-секреты и значения `.env` — разные, не путать «протестировано локально» с «работает в проде».

Health-check: `GET /health`.

## Тесты и линт

```bash
pytest
ruff check .
```

## Деплой (Railway)

Три сервиса из одного репозитория (Railway привязывает один конфиг-файл к одному Root Directory -- отдельного multi-service синтаксиса нет, см. `railway.toml`, читается всеми тремя):

| Сервис | Root directory | Start Command | Примечание |
|---|---|---|---|
| API | `apps/api` | (по умолчанию из `Dockerfile`/`railway.toml`) | `alembic upgrade head` + uvicorn. **Healthcheck Path `/health` -- задать вручную в Settings этого сервиса** (Dashboard-оверрайд, не в `railway.toml`, см. CIN-92: файл общий для всех трёх сервисов, worker/beat не слушают HTTP и падают на healthcheck, если он задан в файле) |
| Worker | `apps/api` | `celery -A app.celery_app worker --loglevel=info` | тот же образ, Custom Start Command в Dashboard, healthcheck НЕ задавать |
| Beat | `apps/api` | `celery -A app.celery_app beat --loglevel=info` | тот же образ, Custom Start Command в Dashboard, healthcheck НЕ задавать; **не может быть на засыпающем тарифе** -- отвечает за запланированные публикации и ежедневный бэкап БД (см. "Бэкапы" ниже) |

Плюс managed-аддоны Postgres и Redis (Railway создаёт `DATABASE_URL`/`REDIS_URL`-совместимые переменные автоматически при подключении аддона -- сверить с именами, которые ждёт `config.py`, при необходимости смэпить вручную).

**Переменные окружения** (см. полный список и комментарии в `.env.example`) -- вводятся в Railway Dashboard, не коммитятся:

```
DATABASE_URL
REDIS_URL
JWT_SECRET
SOCIAL_TOKEN_ENCRYPTION_KEY
CORS_ORIGINS               # https://<домен фронтенда на Vercel>
GEMINI_API_KEY
GEMINI_MODEL
IMAGE_MODEL
VEO_MODEL
TELEGRAM_BOT_TOKEN
META_APP_ID
META_APP_SECRET
META_REDIRECT_URI           # https://<домен API>/oauth/instagram/callback
R2_ACCOUNT_ID
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_BUCKET_NAME
R2_PUBLIC_URL_BASE
PAYPAL_CLIENT_ID             # live, не sandbox
PAYPAL_CLIENT_SECRET         # live
PAYPAL_MODE=live
PAYPAL_PRO_PLAN_ID
PAYPAL_BUSINESS_PLAN_ID
PAYPAL_WEBHOOK_ID             # регистрируется после первого деплоя, когда есть реальный URL
```

## Бэкапы

Railway Trial/Hobby не даёт автоматических бэкапов/PITR для managed Postgres (только на Pro). Вместо апгрейда -- см. CIN-91: `app.scheduler.tasks.backup_database`, Celery beat, ежедневно в 03:00 UTC. Дампает БД через `pg_dump`, гзипует и заливает в тот же R2-бакет, что и медиа (`backups/postgres/YYYY-MM-DD.sql.gz`), храня последние 14 дампов.

Восстановление из дампа:

```bash
# скачать дамп конкретного дня из R2 (например через rclone/aws-cli, настроенный на R2-эндпоинт), затем:
gunzip -c 2026-08-04.sql.gz | psql "$DATABASE_URL"
```

`DATABASE_URL` здесь -- обычный `postgresql://` (без `+psycopg`, это специфика SQLAlchemy-драйвера, `psql`/`pg_dump` его не понимают).
