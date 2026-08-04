import gzip
import subprocess
from datetime import UTC, datetime

import boto3

from app.config import get_settings

BACKUP_PREFIX = "backups/postgres/"
DEFAULT_KEEP = 14


def _r2_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def _pg_dump_url(database_url: str) -> str:
    """pg_dump doesn't understand SQLAlchemy's '+psycopg' driver suffix --
    strip it down to the plain 'postgresql://' scheme it expects."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def dump_database() -> bytes:
    settings = get_settings()
    result = subprocess.run(
        ["pg_dump", _pg_dump_url(settings.database_url), "--format=plain"],
        capture_output=True,
        check=True,
    )
    return gzip.compress(result.stdout)


def upload_backup(data: bytes) -> str:
    """Upload a gzipped dump to R2, keyed by date so re-running the beat
    task on the same day overwrites rather than accumulating."""
    settings = get_settings()
    key = f"{BACKUP_PREFIX}{datetime.now(UTC):%Y-%m-%d}.sql.gz"
    _r2_client().put_object(
        Bucket=settings.r2_bucket_name,
        Key=key,
        Body=data,
        ContentType="application/gzip",
    )
    return key


def rotate_backups(keep: int = DEFAULT_KEEP) -> int:
    """Delete all but the `keep` most recent backups. Returns count deleted."""
    settings = get_settings()
    client = _r2_client()
    response = client.list_objects_v2(Bucket=settings.r2_bucket_name, Prefix=BACKUP_PREFIX)
    objects = sorted(response.get("Contents", []), key=lambda o: o["Key"], reverse=True)
    stale = objects[keep:]
    if not stale:
        return 0
    client.delete_objects(
        Bucket=settings.r2_bucket_name,
        Delete={"Objects": [{"Key": o["Key"]} for o in stale]},
    )
    return len(stale)
