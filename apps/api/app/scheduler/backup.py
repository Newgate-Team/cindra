import gzip
import subprocess
from datetime import UTC, datetime

import boto3
from cryptography.fernet import Fernet

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


def _fernet() -> Fernet:
    # CIN-160: this dump lands in the same publicly-readable bucket as
    # media (see upload_backup) -- fail loudly rather than upload a
    # full plaintext Postgres dump because the key was never set.
    settings = get_settings()
    if not settings.backup_encryption_key:
        raise RuntimeError(
            "BACKUP_ENCRYPTION_KEY не настроен -- бэкап содержит полный дамп БД "
            "и не может быть загружен в публичный R2-бакет без шифрования"
        )
    return Fernet(settings.backup_encryption_key)


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
    """Encrypt a gzipped dump and upload it to R2, keyed by date so
    re-running the beat task on the same day overwrites rather than
    accumulating.

    CIN-160: encrypted, not just gzipped -- this lands in the same R2
    bucket used for public media (see media_storage.py), which has to
    be readable by anyone who knows a key for the app to work at all.
    `.enc` on the key name is deliberate: it's ciphertext, not a
    directly-restorable gzip, so `gunzip` on the raw download fails
    loudly instead of on garbage. To restore: download, then
    `Fernet(BACKUP_ENCRYPTION_KEY).decrypt(data)` before gunzip.
    """
    settings = get_settings()
    encrypted = _fernet().encrypt(data)
    key = f"{BACKUP_PREFIX}{datetime.now(UTC):%Y-%m-%d}.sql.gz.enc"
    _r2_client().put_object(
        Bucket=settings.r2_bucket_name,
        Key=key,
        Body=encrypted,
        ContentType="application/octet-stream",
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
