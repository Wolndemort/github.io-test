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


def test_product_details_migration_exists_and_targets_current_head():
    source = (MIGRATIONS / "4c9d2f1a8b6e_add_details_to_club_products.py").read_text(encoding="utf-8")
    assert 'revision: str = "4c9d2f1a8b6e"' in source
    assert 'down_revision: Union[str, Sequence[str], None] = "i3j4k5l6m7n8"' in source
    assert 'op.add_column("club_products", sa.Column("details", sa.String(length=1000), nullable=True))' in source


def test_subscription_expiry_migration_preserves_calendar_date_and_sets_end_of_day():
    source = (MIGRATIONS / "q8r9s0t1u2v3_normalize_subscription_expiry_dates.py").read_text(encoding="utf-8")
    assert 'down_revision = "p4q5r6s7t8u9"' in source
    assert "date_trunc('day', expire_date)" in source
    assert "23 hours 59 minutes 59 seconds" in source
