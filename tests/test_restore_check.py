import pytest

import scripts.restore_check as restore_check


class DummyProc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_restore_check_success(monkeypatch, tmp_path):
    backup = tmp_path / "backup.sql.gz"
    backup.write_bytes(b"fake")

    monkeypatch.setenv("TEST_DB_HOST", "localhost")
    monkeypatch.setenv("TEST_DB_PORT", "5432")
    monkeypatch.setenv("TEST_DB_NAME", "crm_test")
    monkeypatch.setenv("TEST_DB_USER", "postgres")
    monkeypatch.setenv("TEST_DB_PASSWORD", "secret")

    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return DummyProc()

    monkeypatch.setattr(restore_check.asyncio, "create_subprocess_exec", fake_exec)

    rc = await restore_check.restore_check(str(backup))

    assert rc == 0
    assert calls[0][0] == "dropdb"
    assert calls[1][0] == "createdb"
    assert calls[2][0] == "pg_restore"


@pytest.mark.asyncio
async def test_restore_check_fails_when_backup_missing(monkeypatch):
    monkeypatch.setenv("TEST_DB_HOST", "localhost")
    monkeypatch.setenv("TEST_DB_PORT", "5432")
    monkeypatch.setenv("TEST_DB_NAME", "crm_test")
    monkeypatch.setenv("TEST_DB_USER", "postgres")
    monkeypatch.setenv("TEST_DB_PASSWORD", "secret")

    rc = await restore_check.restore_check("does-not-exist.sql.gz")

    assert rc == 1


@pytest.mark.asyncio
async def test_restore_check_propagates_dropdb_error(monkeypatch, tmp_path):
    backup = tmp_path / "backup.sql.gz"
    backup.write_bytes(b"fake")

    monkeypatch.setenv("TEST_DB_HOST", "localhost")
    monkeypatch.setenv("TEST_DB_PORT", "5432")
    monkeypatch.setenv("TEST_DB_NAME", "crm_test")
    monkeypatch.setenv("TEST_DB_USER", "postgres")
    monkeypatch.setenv("TEST_DB_PASSWORD", "secret")

    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if args[0] == "dropdb":
            return DummyProc(returncode=1, stderr=b"drop failed")
        return DummyProc()

    monkeypatch.setattr(restore_check.asyncio, "create_subprocess_exec", fake_exec)

    rc = await restore_check.restore_check(str(backup))

    assert rc == 1
    assert calls[0][0] == "dropdb"
