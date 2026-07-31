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
