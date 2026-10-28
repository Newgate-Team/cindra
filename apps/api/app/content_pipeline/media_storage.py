import shutil
import uuid
from pathlib import Path

import boto3

from app.config import get_settings


def _client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        # R2 is S3-compatible and doesn't have real AWS regions --
        # "auto" is Cloudflare's documented value for this.
        region_name="auto",
    )


def upload_bytes(data: bytes, content_type: str, extension: str) -> str:
    """Upload generated image/video bytes to R2, return the public URL.

    Without real R2 credentials configured (see CIN-56/CIN-78 gate
    ticket, r2_account_id empty by default) this fails loudly and
    immediately -- boto3 rejects the resulting endpoint URL
    (https://.r2.cloudflarestorage.com) at client construction with a
    ValueError, before any network call. Not mocked away, just an
    earlier failure point than the HTTP auth errors the other
    integrations in this project hit (their endpoint host doesn't
    depend on the credential the way R2's does).
    """
    settings = get_settings()
    key = f"{uuid.uuid4()}.{extension}"
    if settings.media_local_dir:
        # Dev-only stand-in for R2 -- see Settings.media_local_dir.
        directory = Path(settings.media_local_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / key).write_bytes(data)
        return f"{settings.r2_public_url_base.rstrip('/')}/{key}"
    _client().put_object(
        Bucket=settings.r2_bucket_name,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return f"{settings.r2_public_url_base}/{key}"


def upload_file(
    path: Path, content_type: str, extension: str, *, download_name: str | None = None
) -> str:
    """upload_bytes for files too big to hold in memory (a montage
    package runs to gigabytes): boto3 streams it from disk in parts.
    `download_name` (ASCII) is what the browser saves it as."""
    settings = get_settings()
    key = f"{uuid.uuid4()}.{extension}"
    if settings.media_local_dir:
        directory = Path(settings.media_local_dir)
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, directory / key)
        return f"{settings.r2_public_url_base.rstrip('/')}/{key}"
    extra = {"ContentType": content_type}
    if download_name:
        extra["ContentDisposition"] = f'attachment; filename="{download_name}"'
    _client().upload_file(str(path), settings.r2_bucket_name, key, ExtraArgs=extra)
    return f"{settings.r2_public_url_base}/{key}"
