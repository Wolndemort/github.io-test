from pathlib import Path


def test_release_readiness_keeps_production_gates_explicit():
    source = Path("RELEASE_READINESS.md").read_text(encoding="utf-8")
    assert "Backup and rollback verification" in source
    assert "Separate explicit approval for production deployment" in source
    assert "Do not push to `master`" in source


def test_backup_restore_check_is_test_database_only():
    source = Path("scripts/restore_check.py").read_text(encoding="utf-8")
    assert 'TEST_DB_HOST' in source
    assert 'TEST_DB_NAME' in source
    assert 'dropdb' in source and 'createdb' in source and 'pg_restore' in source
    assert 'DATABASE_URL' not in source


def test_native_email_migration_has_reversible_nullable_upgrade():
    source = Path("migrations/versions/z8a9b0c1d2e3_add_user_email_for_web_auth.py").read_text(encoding="utf-8")
    assert 'down_revision = "y7z8a9b0c1d2"' in source
    assert 'nullable=True' in source
    assert 'op.drop_column("users", "email")' in source
