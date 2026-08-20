from pathlib import Path


def test_user_email_is_nullable_and_migration_is_reversible():
    model = Path("database/db.py").read_text(encoding="utf-8")
    migration = Path("migrations/versions/z8a9b0c1d2e3_add_user_email_for_web_auth.py").read_text(encoding="utf-8")
    assert 'email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)' in model
    assert 'op.add_column("users"' in migration
    assert 'nullable=True' in migration
    assert 'op.drop_column("users", "email")' in migration
