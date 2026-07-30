from pathlib import Path


def _block():
    source = Path("admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    start = source.index("async def webapp_create_student_submit")
    end = source.index('@router.get("/webapp/client-cabinet/freeze"', start)
    return source[start:end]


def test_webapp_creation_matches_client_cabinet_student_defaults():
    block = _block()
    assert "parent_id=user_id" in block
    assert "balance_lessons=0" in block
    assert "expire_date=None" in block
    assert "can_freeze=1" in block
    assert "is_frozen=0" in block
    assert "discipline=_default_student_discipline" in block
    assert "student.name.strip().casefold() == name.casefold()" in block
    assert "student.birthday == birthday" in block


def test_webapp_creation_supports_two_distinct_normalized_parent_phones():
    block = _block()
    assert "phone_secondary" in block
    assert "parent_phone_secondary=secondary_phone" in block
    assert "primary_phone == secondary_phone" in block
    assert "Номера родителей должны отличаться" in block
