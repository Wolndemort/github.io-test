import asyncio
import os
import sys
from pathlib import Path

from loguru import logger


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required env var: {name}")
    return value


async def _run_cmd(*args: str, env: dict[str, str] | None = None) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env or os.environ.copy(),
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout, stderr


async def restore_check(backup_path: str) -> int:
    path = Path(backup_path)
    if not path.exists():
        logger.error(f"Backup file not found: {path}")
        return 1

    test_host = _required_env("TEST_DB_HOST")
    test_port = _required_env("TEST_DB_PORT")
    test_db = _required_env("TEST_DB_NAME")
    test_user = _required_env("TEST_DB_USER")
    test_password = _required_env("TEST_DB_PASSWORD")

    logger.info(f"Using test database: {test_host}:{test_port}/{test_db}")

    env = os.environ.copy()
    env["PGPASSWORD"] = test_password

    drop_rc, _, drop_err = await _run_cmd(
        "dropdb",
        "-h",
        test_host,
        "-p",
        test_port,
        "-U",
        test_user,
        "--if-exists",
        test_db,
        env=env,
    )
    if drop_rc != 0:
        logger.error(drop_err.decode(errors="ignore").strip() or "dropdb failed")
        return drop_rc

    create_rc, _, create_err = await _run_cmd(
        "createdb",
        "-h",
        test_host,
        "-p",
        test_port,
        "-U",
        test_user,
        test_db,
        env=env,
    )
    if create_rc != 0:
        logger.error(create_err.decode(errors="ignore").strip() or "createdb failed")
        return create_rc

    restore_rc, restore_out, restore_err = await _run_cmd(
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "-h",
        test_host,
        "-p",
        test_port,
        "-U",
        test_user,
        "-d",
        test_db,
        str(path),
        env=env,
    )
    if restore_rc != 0:
        logger.error(restore_out.decode(errors="ignore").strip())
        logger.error(restore_err.decode(errors="ignore").strip() or "pg_restore failed")
        return restore_rc

    logger.info("Restore check completed successfully")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/restore_check.py <backup_file>", file=sys.stderr)
        return 2
    return asyncio.run(restore_check(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
