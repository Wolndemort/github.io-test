from pathlib import Path


def test_staging_fixture_helpers_are_explicitly_staging_scoped():
    seed = Path("scripts/seed_staging_client.sh").read_text(encoding="utf-8")
    cleanup = Path("scripts/cleanup_staging_client.sh").read_text(encoding="utf-8")
    for script in (seed, cleanup):
        assert "speedycrm_staging_db" in script
        assert "/root/speedycrm-staging" in script
        assert "speedycrm-staging" in script
        assert "github.io-test" not in script
        assert "/root/alter" not in script


def test_staging_fixture_uses_reserved_identity_and_is_reversible():
    seed = Path("scripts/seed_staging_client.sh").read_text(encoding="utf-8")
    cleanup = Path("scripts/cleanup_staging_client.sh").read_text(encoding="utf-8")
    assert "990000001" in seed and "990000001" in cleanup
    assert "DELETE FROM students WHERE parent_id = 990000001" in cleanup
    assert "DELETE FROM users WHERE user_id = 990000001" in cleanup


def test_client_read_only_routes_use_authenticated_actor_scope():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    for name in ("client_cabinet_data", "client_schedule_data", "client_products_data", "client_legal_data", "client_me"):
        start = source.index(f"async def {name}")
        end = source.find("\nasync def ", start + 1)
        block = source[start:] if end == -1 else source[start:end]
        assert "require_web_context(context)" in block
        assert "actor.club_id" in block
        assert '"read_only": True' in block


def test_client_cross_club_access_is_denied_before_data_lookup():
    source = Path("auth/routes.py").read_text(encoding="utf-8")
    start = source.index("async def native_verify")
    block = source[start:source.index("@router.get(\"/web-entry", start)]
    assert "User.club_id == payload.club_id" in block
    assert "not user or not await consume_otp" in block
