# apps/api

Backend Cindra: FastAPI + Python.

## Разработка

```bash
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Health-check: `GET /health`.

## Тесты и линт

```bash
pytest
ruff check .
```
