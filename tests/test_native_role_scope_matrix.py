from pathlib import Path


def test_native_auth_has_owner_staff_client_role_matrix():
    source = Path("auth/routes.py").read_text(encoding="utf-8")
    start = source.index("async def native_verify")
    end = source.index('@router.post("/web-entry"', start) if '@router.post("/web-entry"' in source[start:] else source.index('@router.get("/web-entry"', start)
    block = source[start:end]
    assert 'actor_type, role, permissions = "owner", "owner", frozenset()' in block
    assert 'actor_type, role, permissions = "staff", staff.role' in block
    assert 'actor_type, role, permissions = "client", "client", frozenset()' in block
    assert 'User.club_id == payload.club_id' in block


def test_telegram_client_fallback_is_club_scoped():
    source = Path("auth/routes.py").read_text(encoding="utf-8")
    start = source.index("async def telegram_exchange")
    end = source.index('@router.get("/me")', start)
    block = source[start:end]
    assert "User.user_id == user_id, User.club_id == club.id" in block
    assert "Student.club_id == club.id, Student.parent_id == user_id" in block
    assert "not staff and not client_user and not client_student" in block
