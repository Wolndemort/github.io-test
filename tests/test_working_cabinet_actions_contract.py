from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_working_cabinet_exposes_staff_motivation_for_staff_mode():
    template = (ROOT / "templates" / "client_cabinet.html").read_text(encoding="utf-8")

    assert "{% if is_staff_mode %}" in template
    assert 'href="/webapp/staff-motivation?club_id={{ club_id }}"' in template


def test_product_sale_has_only_working_cabinet_back_link():
    template = (ROOT / "templates" / "admin_product_sale.html").read_text(encoding="utf-8")

    assert "← В рабочий кабинет" in template
    assert "← В панель" not in template
    assert 'href="/admin?club_id=' not in template


def test_standard_staff_roles_can_open_own_motivation():
    source = (ROOT / "admin_module" / "webapp_views.py").read_text(encoding="utf-8")

    assert 'async def staff_motivation_page' in source
    assert 'verify_telegram_data(init_data, club.bot_token if club else "")' in source


def test_working_sections_keep_single_working_cabinet_back_action():
    checks = {
        "cash_register.html": ("← В панель",),
        "admin_sales.html": ("← Атлеты",),
        "admin_visits.html": ("/webapp/admin?", "/webapp/admin-audit?", "/webapp/admin-visits?"),
        "admin_audit.html": ("/admin?", "/webapp/admin-visits?", "/webapp/admin-legal?"),
        "admin_work_schedule.html": ("/admin?", "/webapp/admin-schedule?"),
    }
    for name, forbidden in checks.items():
        template = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert "_staff_back_button.html" in template or "рабочий кабинет" in template.lower()
        for text in forbidden:
            assert text not in template


def test_freeze_and_tariffs_use_working_cabinet_back_action_once():
    freeze = (ROOT / "templates" / "admin_freeze.html").read_text(encoding="utf-8")
    tariffs = (ROOT / "templates" / "admin_tariffs.html").read_text(encoding="utf-8")

    assert "staff-pass" not in freeze
    assert freeze.count("рабочий кабинет") == 1
    assert tariffs.count("_staff_back_button.html") == 1
