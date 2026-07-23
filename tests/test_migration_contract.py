from pathlib import Path


MIGRATIONS = Path(__file__).parents[1] / "migrations" / "versions"


def test_payment_migration_keeps_legacy_parent_revision():
    """Published revision history must remain compatible with existing DBs."""
    source = (MIGRATIONS / "c3d9e7a1b2f4_add_payment_provider_id.py").read_text(encoding="utf-8")
    assert 'revision = "c3d9e7a1b2f4"' in source
    assert 'down_revision = "7f725dd0b763"' in source


def test_payment_migration_is_guarded_for_existing_tables():
    source = (MIGRATIONS / "c3d9e7a1b2f4_add_payment_provider_id.py").read_text(encoding="utf-8")
    assert '"payment_orders" not in inspector.get_table_names()' in source
    assert '"subscriptions" not in inspector.get_table_names()' in source
    assert '"visit_logs" not in inspector.get_table_names()' in source
    assert '"provider_payment_id" not in' in source
