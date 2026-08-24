from pathlib import Path

def test_admin_freeze_screen_supports_manager_and_custom_end_date():
    source = Path("admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    page = Path("templates/admin_freeze.html").read_text(encoding="utf-8")
    assert '@router.get("/webapp/admin-freeze"' in source
    assert '@router.post("/webapp/admin-freeze"' in source
    assert "verify_webapp_admin_or_manager" in source
    assert 'action == "freeze"' in source
    assert 'action == "unfreeze"' in source
    assert "date_to" in source and "date_to" in page
    assert "Заморозить" in page
    assert "Разморозить" in page

def test_admin_dashboard_exposes_existing_admin_freeze_link():
    page = Path("templates/admin.html").read_text(encoding="utf-8")
    assert "/webapp/admin-freeze" in page
