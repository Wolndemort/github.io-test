from pathlib import Path


def test_telegram_exchange_has_scoped_client_fallback_after_staff_check():
    source = Path("auth/routes.py").read_text(encoding="utf-8")
    start = source.index("async def telegram_exchange")
    end = source.index('@router.get("/me")', start)
    block = source[start:end]
    assert "User.user_id == user_id, User.club_id == club.id" in block
    assert "Student.club_id == club.id, Student.parent_id == user_id" in block
    assert 'actor_type = "client"' in block
    assert 'actor_type = "staff"' in block
