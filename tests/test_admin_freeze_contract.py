from pathlib import Path


def test_admin_freeze_screen_is_owner_only_and_has_both_actions():
    source = Path("admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    page = Path("templates/admin_freeze.html").read_text(encoding="utf-8")
    assert '@router.get("/webapp/admin-freeze"' in source
    assert '@router.post("/webapp/admin-freeze"' in source
    assert "await verify_webapp_admin(club, init_data)" in source
    assert 'action == "freeze"' in source
    assert 'action == "unfreeze"' in source
    assert "Заморозить" in page
    assert "Разморозить" in page


def test_admin_dashboard_exposes_admin_freeze_link():
    page = Path("templates/admin.html").read_text(encoding="utf-8")
    staff_page = Path("templates/staff_pass.html").read_text(encoding="utf-8")
    assert "/webapp/admin-freeze" in page
    assert "Заморозка атлетов" in page
    assert "/webapp/admin-freeze" not in staff_page
