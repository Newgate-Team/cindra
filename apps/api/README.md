# apps/api

Backend Cindra: FastAPI + Python.

## Разработка

```bash
uv sync
uv run uvicorn app.main:app --reload
```

Health-check: `GET /health`.

## Тесты

```bash
uv run pytest
```
