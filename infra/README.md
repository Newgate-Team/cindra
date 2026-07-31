# infra

IaC, деплой-конфиги, миграции БД.

## Локальная среда

```bash
docker compose -f infra/docker-compose.yml up -d
docker compose -f infra/docker-compose.yml down
```

Поднимает Postgres (`localhost:5432`, БД/юзер/пароль `cindra`) и Redis (`localhost:6380` — нестандартный порт на хосте, чтобы не конфликтовать с локально запущенным Redis других проектов; внутри docker-сети контейнеры видят друг друга по умолчанию на `6379`). Секреты для локальной разработки — в `apps/api/.env` (gitignored, см. `.env.example`), не в закоммиченных файлах.

