# infra

IaC, деплой-конфиги, миграции БД.

## Локальная среда

```bash
docker compose -f infra/docker-compose.yml up -d
docker compose -f infra/docker-compose.yml down
```

Поднимает Postgres (`localhost:5433`, БД/юзер/пароль `cindra`) и Redis (`localhost:6380`) — оба на нестандартных хостовых портах, чтобы не конфликтовать с локально запущенными Postgres/Redis других проектов на этой машине (внутри docker-сети контейнеры видят друг друга по умолчанию на `5432`/`6379`). Секреты для локальной разработки — в `apps/api/.env` (gitignored, см. `.env.example`), не в закоммиченных файлах.

