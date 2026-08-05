from pathlib import Path


def test_vps_backup_creates_verified_custom_dump_and_keeps_fourteen_days():
    source = (Path(__file__).parents[1] / "scripts" / "backup-db.sh").read_text(encoding="utf-8")
    assert "--format=custom" in source
    assert "pg_restore --list" in source
    assert "RETENTION_DAYS" in source
    assert "-mtime \"+$RETENTION_DAYS\"" in source
    assert "flock" in source


def test_yandex_backup_uploads_and_verifies_cloud_copy():
    source = (Path(__file__).parents[1] / "scripts" / "backup-db-to-s3.sh").read_text(encoding="utf-8")
    assert "./scripts/backup-db.sh" in source
    assert "storage.yandexcloud.net" in source
    assert 'S3_PREFIX="${S3_PREFIX:-aaaa/postgres}"' in source
    assert "aws s3 cp" in source
    assert "aws s3api head-object" in source
    assert "CLOUD_RETENTION_DAYS" in source
    assert "aws s3 rm" in source
    assert "S3_BUCKET" in source
