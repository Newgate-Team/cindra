FROM python:3.12-slim

WORKDIR /app

# psycopg[binary] and cryptography ship prebuilt wheels for this base
# image -- no build-essential/libpq-dev needed, keeps the image small.
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir .

# Railway/Render both inject PORT at runtime -- the shell form lets
# $PORT expand, unlike exec-form CMD.
# Default process is the API; Celery worker/beat services (see
# railway.toml comments) override this with a Custom Start Command
# pointing at the same image instead of building their own.
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
