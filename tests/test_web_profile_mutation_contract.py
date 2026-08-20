from pathlib import Path


def test_client_profile_mutation_is_self_scoped_and_otp_separate():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@client_router.patch("/me")'); block = source[start:source.index('@client_router.get("/legal/data")', start)]
    for value in ("WEB_PROFILE_MUTATIONS_ENABLED", "require_csrf", "User.user_id == actor.user_id", "User.club_id == actor.club_id", "with_for_update()", "web:profile:", "web_profile_updated"):
        assert value in block
    assert "email" not in block
