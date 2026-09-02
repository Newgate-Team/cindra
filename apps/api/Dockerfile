FROM python:3.12-slim

WORKDIR /app

# psycopg[binary] and cryptography ship prebuilt wheels for this base
# image -- no build-essential/libpq-dev needed, keeps the image small.
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

# postgresql-client provides pg_dump for the CIN-91 backup task (Celery
# beat, daily). Debian bookworm's default postgresql-client version may
# lag Railway's Postgres server version -- pg_dump reading from a newer
# server usually works within a few major versions but isn't guaranteed;
# revisit if backups start failing after a Postgres major version bump.
#
# fonts-dejavu-core is what the CIN-148 template renderer draws with:
# Pillow's built-in font has no Cyrillic at all (Russian comes out as
# empty boxes -- verified), and this package is ~3 MB from Debian main.
# IBM Plex would match the web app's typeface, but Debian ships it in
# contrib, which this base image doesn't enable; point LAYOUT_FONT_* at
# it instead if it's ever installed on the host.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir .

# Railway/Render both inject PORT at runtime -- the shell form lets
# $PORT expand, unlike exec-form CMD.
# Default process is the API; Celery worker/beat services (see
# railway.toml comments) override this with a Custom Start Command
# pointing at the same image instead of building their own.
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
