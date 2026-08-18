from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_web_discount_management_has_shared_settings_and_both_value_types():
    views = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    page = (ROOT / "templates/admin_discounts.html").read_text(encoding="utf-8")
    cabinet = (ROOT / "templates/client_cabinet.html").read_text(encoding="utf-8")
    assert '"/webapp/admin-discounts"' in views
    assert '"/webapp/admin-discounts/change"' in views
    assert "DiscountAssignment" in views
    assert "scope" in views
    assert 'value="percent"' in page
    assert 'value="fixed"' in page
    assert 'data-del' in page and "Удалить" in page
    assert "admin-discounts" in cabinet


def test_bot_discount_fsm_uses_the_same_club_settings_key_and_supports_cashier():
    panel = (ROOT / "handlers/admin_settings_panel.py").read_text(encoding="utf-8")
    states = (ROOT / "handlers/states.py").read_text(encoding="utf-8")
    assert 'callback_data="admin_discounts"' in panel
    assert "AdminSettingsSG.waiting_for_discount" in panel
    assert 'settings["discounts"]' in panel
    assert '"cash_sale"' in panel
    assert "процент/сумма" in panel
    assert "waiting_for_discount = State()" in states


def test_checkout_uses_the_shared_discount_service():
    checkout = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    assert "active_discount" in checkout
    assert "apply_discount" in checkout
    assert "original_amount_kopecks" in checkout

def test_discount_application_is_scoped_and_order_keeps_snapshot_fields():
    service = (ROOT / "services/discounts.py").read_text(encoding="utf-8")
    db = (ROOT / "database/db.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/versions/u3v4w5x6y7z8_add_payment_discount_snapshot.py").read_text(encoding="utf-8")
    assert 'Discount.scope.in_([scope, "all"])' in service
    assert "original_amount_kopecks" in db
    assert "discount_amount_kopecks" in db
    assert "discount_name" in migration

def test_cart_recalculates_each_line_by_its_discount_scope():
    api = (ROOT / "admin_module/api.py").read_text(encoding="utf-8")
    db = (ROOT / "database/db.py").read_text(encoding="utf-8")
    assert 'active_discount(session, club.id, int(tg_user["id"]), "products")' in api
    assert 'active_discount(session, club.id, int(tg_user["id"]), "subscriptions", student.id)' in api
    assert "original_amount_kopecks" in db
    assert "discount_amount_kopecks" in db

def test_students_without_profiles_can_own_discount_assignments():
    db = (ROOT / "database/db.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/versions/x6y7z8a9b0c1_add_student_discount_assignments.py").read_text(encoding="utf-8")
    service = (ROOT / "services/discounts.py").read_text(encoding="utf-8")
    assert "user_id: Mapped[Optional[int]]" in db
    assert "student_id: Mapped[Optional[int]]" in db
    assert "allow discounts for students without a profile" in migration
    assert "student_id: int | None = None" in service

def test_discount_is_visible_in_web_countdown_and_telegram_profile():
    web = (ROOT / "templates/client_cabinet.html").read_text(encoding="utf-8")
    cabinet = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    bot = (ROOT / "handlers/user_option.py").read_text(encoding="utf-8")
    assert "profile_discount" in cabinet
    assert "discount-countdown" in web
    assert "setInterval(tick,1000)" in web
    assert "Ваша скидка" in bot

def test_discount_notifications_are_scheduled_and_idempotent():
    jobs = (ROOT / "services/scheduler_jobs.py").read_text(encoding="utf-8")
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    views = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    assert "send_discount_reminders" in jobs
    assert "notify:discount-monthly" in jobs
    assert "notify:discount-expiry" in jobs
    assert "_notification_once" in jobs
    assert "discount_reminders" in main
    assert "уведомление о скидке" in views
