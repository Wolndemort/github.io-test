from pathlib import Path


def test_production_deploy_workflow_is_not_triggered_by_migration_branch():
    source = Path(".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "branches: [ master ]" in source
    assert "github.ref == 'refs/heads/master'" in source
    assert "github.ref == 'refs/heads/main'" in source
    assert "web-migration/phase-0-auth" not in source


def test_deploy_workflow_has_backup_health_and_rollback_hooks():
    source = Path(".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "pg_dump" in source
    assert "rollback()" in source
    assert "alembic upgrade heads" in source
    assert "/health" in source and "/ready" in source
