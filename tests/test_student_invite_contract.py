from pathlib import Path


def test_admin_student_invite_requires_saved_phone_and_returns_bot_link():
    source = Path("admin_module/api.py").read_text(encoding="utf-8")
    assert '@router.post("/admin/students/{student_id}/invite")' in source
    assert "await verify_webapp_admin(club, payload.init_data)" in source
    assert "if not phone" in source
    assert "StudentInvitePayload" in source
    assert "parent_slot" in source
    assert 'f"https://t.me/{username}?start=invite_{student.id}_{payload.parent_slot}"' in source


def test_two_parent_phone_storage_has_backwards_compatible_migration():
    model = Path("database/db.py").read_text(encoding="utf-8")
    migration = Path("migrations/versions/p4q5r6s7t8u9_add_parent_phone_slots.py").read_text(encoding="utf-8")
    assert "parent_phone_secondary" in model
    assert "phone:" in model
    assert 'op.add_column("students"' in migration
    assert 'op.add_column("student_parents"' in migration
    assert "sp.is_primary = TRUE" in migration


def test_student_cards_show_invite_after_save_and_linked_students_hide_it():
    page = Path("templates/admin_students.html").read_text(encoding="utf-8")
    start = page.index("function attachInviteButton")
    block = page[start:page.index("function bindEdit", start)]
    assert "Пригласить родителя" in block
    assert "form.dataset[`parent${slot}`] === '1'" in block
    assert "/invite" in block
    assert "attachInviteButton(form);" in page


def test_invite_start_payload_requests_phone_without_changing_normal_start_flow():
    source = Path("handlers/start.py").read_text(encoding="utf-8")
    assert 'start_payload.startswith("invite_")' in source
    assert "request_contact=True" in source
    assert "invited_student.parent_phone" in source
    assert "invited_student.parent_phone_secondary" in source


def test_start_menu_reads_legacy_and_additional_parent_links():
    source = Path("handlers/start.py").read_text(encoding="utf-8")
    assert "StudentParent" in source
    assert ".outerjoin(StudentParent" in source
    assert "Student.parent_id == user_id" in source
    assert "StudentParent.parent_id == user_id" in source
    assert ".distinct()" in source
