import gzip
import subprocess

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.scheduler import backup
from app.scheduler.tasks import backup_database

_TEST_BACKUP_KEY = Fernet.generate_key().decode()


class _FakeR2Client:
    def __init__(self):
        self.uploads: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put_object(self, Bucket, Key, Body, ContentType):
        self.uploads[Key] = Body

    def list_objects_v2(self, Bucket, Prefix):
        return {"Contents": [{"Key": key} for key in self.uploads if key.startswith(Prefix)]}

    def delete_objects(self, Bucket, Delete):
        for obj in Delete["Objects"]:
            self.deleted.append(obj["Key"])
            self.uploads.pop(obj["Key"], None)


@pytest.fixture
def fake_r2(monkeypatch) -> _FakeR2Client:
    client = _FakeR2Client()
    monkeypatch.setattr(backup, "_r2_client", lambda: client)
    return client


@pytest.fixture
def backup_key(monkeypatch) -> str:
    monkeypatch.setattr(get_settings(), "backup_encryption_key", _TEST_BACKUP_KEY)
    return _TEST_BACKUP_KEY


def test_pg_dump_url_strips_driver_suffix() -> None:
    assert backup._pg_dump_url("postgresql+psycopg://u:p@host:5432/db") == (
        "postgresql://u:p@host:5432/db"
    )


def test_dump_database_gzips_pg_dump_stdout(monkeypatch) -> None:
    def _fake_run(cmd, capture_output, check):
        assert cmd[0] == "pg_dump"
        return subprocess.CompletedProcess(cmd, 0, stdout=b"-- sql dump", stderr=b"")

    monkeypatch.setattr(backup.subprocess, "run", _fake_run)

    result = backup.dump_database()

    assert gzip.decompress(result) == b"-- sql dump"


def test_upload_backup_keys_by_date(fake_r2: _FakeR2Client, backup_key: str) -> None:
    key = backup.upload_backup(b"gzipped bytes")

    assert key.startswith(backup.BACKUP_PREFIX)
    assert key.endswith(".sql.gz.enc")
    # CIN-160: must not be the plaintext dump -- this lands in the same
    # publicly-readable bucket as media.
    assert fake_r2.uploads[key] != b"gzipped bytes"
    assert Fernet(backup_key).decrypt(fake_r2.uploads[key]) == b"gzipped bytes"


def test_upload_backup_raises_without_encryption_key(fake_r2: _FakeR2Client) -> None:
    # No backup_key fixture here -- backup_encryption_key defaults to "".
    with pytest.raises(RuntimeError, match="BACKUP_ENCRYPTION_KEY"):
        backup.upload_backup(b"gzipped bytes")
    assert fake_r2.uploads == {}


def test_upload_backup_ciphertext_is_not_decryptable_with_a_different_key(
    fake_r2: _FakeR2Client, backup_key: str
) -> None:
    key = backup.upload_backup(b"gzipped bytes")
    with pytest.raises(InvalidToken):
        Fernet(Fernet.generate_key()).decrypt(fake_r2.uploads[key])


def test_rotate_backups_keeps_only_the_newest(fake_r2: _FakeR2Client) -> None:
    for day in range(1, 6):
        fake_r2.uploads[f"{backup.BACKUP_PREFIX}2026-08-0{day}.sql.gz"] = b"x"

    deleted = backup.rotate_backups(keep=2)

    assert deleted == 3
    assert sorted(fake_r2.uploads) == [
        f"{backup.BACKUP_PREFIX}2026-08-04.sql.gz",
        f"{backup.BACKUP_PREFIX}2026-08-05.sql.gz",
    ]


def test_rotate_backups_is_a_noop_under_the_limit(fake_r2: _FakeR2Client) -> None:
    fake_r2.uploads[f"{backup.BACKUP_PREFIX}2026-08-01.sql.gz"] = b"x"

    assert backup.rotate_backups(keep=14) == 0
    assert fake_r2.deleted == []


def test_backup_database_task_dumps_uploads_and_rotates(
    monkeypatch, fake_r2: _FakeR2Client, backup_key: str
) -> None:
    monkeypatch.setattr(backup, "dump_database", lambda: b"dump-bytes")

    result = backup_database.apply().get()

    assert result.startswith(backup.BACKUP_PREFIX)
    assert Fernet(backup_key).decrypt(fake_r2.uploads[result]) == b"dump-bytes"
