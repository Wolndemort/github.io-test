import pytest
from pathlib import Path
from pydantic import ValidationError

from admin_module.api import router
from admin_module.schemas import AdminStudentUpdate
from admin_module.system_api import StudentCreate
from database.constants import DEFAULT_CLUB_SETTINGS
from handlers.super_admin_handlers import merge_default_club_settings
from services.input_normalization import normalize_ru_phone, parse_user_date


def route_keys():
    result = set()
    for route in router.routes:
        for method in getattr(route, "methods", None) or {"GET"}:
            result.add((route.path, method))
    return result


def test_club_admin_management_routes_are_registered():
    keys = route_keys()
    assert ("/admin/students", "GET") in keys
    assert ("/admin/students/{student_id}", "PATCH") in keys
    assert ("/admin/students", "POST") in keys
    assert ("/admin/sales", "GET") in keys


def test_admin_product_cash_sale_uses_cart_and_confirms_stock_sale():
    keys = route_keys()
    assert ("/webapp/admin-product-sale", "GET") in keys
    assert ("/webapp/admin-product-sale", "POST") in keys
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    page = Path("templates/admin_product_sale.html").read_text(encoding="utf-8")
    buttons = Path("handlers/admin_option.py").read_text(encoding="utf-8")
    main_buttons = Path("handlers/buttons.py").read_text(encoding="utf-8")
    assert "status=\"CONFIRMED\"" in api
    assert "product.stock -= quantity" in api
    assert "item_type=\"product\"" in api
    assert "оплату наличными" in page
    assert "admin-product-sale" in buttons
    assert "Продать товар" in main_buttons
    assert '"image_url": p.image_url' in api


def test_admin_sales_and_student_filters_are_present():
    students = Path("templates/admin_students.html").read_text(encoding="utf-8")
    sales = Path("templates/admin_sales.html").read_text(encoding="utf-8")
    assert "student-filter" in students
    assert "data-discipline" in students
    assert "/admin/sales" in students
    assert "payment_method" in sales
    assert "date_from" in sales and "date_to" in sales
    assert "weekday" in sales
    assert "category" in sales
    assert "discipline" in sales
    assert "0–6 лет" in students and "18+ лет" in students
    assert "Выручка за период" in sales
    assert "Средний чек" in sales


def test_stats_and_sales_templates_have_readable_dark_surface_metrics():
    stats = Path("templates/stats.html").read_text(encoding="utf-8")
    css = Path("static/css/mono.css").read_text(encoding="utf-8")
    assert "revenue_today" in stats and "revenue_week" in stats and "revenue_month" in stats
    assert "empty-note" in stats and "muted-note" in stats
    assert ".mono-card .list-item span" in css
    assert "color: #f7f7f7 !important" in css


def test_admin_and_stats_chat_links_are_parent_scoped_and_consistent():
    admin = Path("templates/admin.html").read_text(encoding="utf-8")
    students = Path("templates/admin_students.html").read_text(encoding="utf-8")
    stats = Path("templates/stats.html").read_text(encoding="utf-8")
    admin_pages = Path("admin_module/admin_pages.py").read_text(encoding="utf-8")
    analytics = Path("services/analytics.py").read_text(encoding="utf-8")

    assert "student.username" not in admin
    assert "Проверить чат" not in admin
    assert "💬 Чат" in admin
    assert "openChat(" in admin

    assert "💬 Чат" in students
    assert "openChat(" in students
    assert "Написать в Telegram" not in students

    assert "💬 Чат" in stats
    assert "parent_id" in stats
    assert "openChat(" in stats

    assert '"parent_id": student.parent_id' in admin_pages or '"parent_id": getattr(s, "parent_id", None)' in admin_pages
    assert '"parent_id": getattr(student, "parent_id", None)' in analytics
    assert '"username":' not in analytics


def test_client_webapp_can_create_students_but_cannot_edit_or_delete_them():
    cabinet = Path("admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    page = Path("templates/client_cabinet.html").read_text(encoding="utf-8")
    form = Path("templates/webapp_create_student.html").read_text(encoding="utf-8")

    assert "/webapp/client-cabinet/create-student" in cabinet
    assert "WebAppCreateStudentPayload" in cabinet
    assert "webapp_student_created" in cabinet
    assert "Создать атлета" in page
    assert "create-student" in page
    assert "Редактировать" not in page
    assert "Удалить" not in page
    assert "Только создание" in form
    assert "fetch('/webapp/client-cabinet/create-student'" in form
    assert "update-student" not in form
    assert "delete-student" not in form
    assert 'input id="birthday" type="text"' in form
    assert "replace(/\\D/g, '')" in form
    assert "birthdayPicker" in form
    assert "showPicker" in form


def test_boxing_name_no_longer_contains_children_suffix():
    constants = Path("database/constants.py").read_text(encoding="utf-8")
    admin_pages = Path("admin_module/admin_pages.py").read_text(encoding="utf-8")
    assert "Бокс (Дети)" not in constants
    assert "Бокс (Дети)" not in admin_pages
    assert '"boxing": {' in constants
    assert '"boxing": "🥊 Бокс"' in admin_pages


def test_admin_student_dates_are_text_inputs_with_autofill_formatting():
    page = Path("templates/admin_students.html").read_text(encoding="utf-8")
    assert 'id="newBirthday" type="text"' in page
    assert 'input[name="birthday"]' in page
    assert 'input[name="expire_date"]' in page
    assert "replace(/\\D/g, '')" in page
    assert "showPicker" in page
    assert "birthday-picker" in page
    assert "formatDMY" in page
    assert "danger-delete" in page
    assert "init_data: initData" in page


def test_stats_freeze_dropdown_has_no_visible_title_and_revenue_has_date_filters():
    stats = Path("templates/stats.html").read_text(encoding="utf-8")
    revenue = Path("admin_module/api.py").read_text(encoding="utf-8")
    assert "Кто именно" not in stats
    assert "athlete-dropdown" in stats
    assert "Заморозки" in stats
    assert "Выручка по датам" in stats
    assert 'name="date_from"' in stats
    assert 'name="date_to"' in stats
    assert "date_from: str | None = Query(default=None)" in revenue
    assert "date_to: str | None = Query(default=None)" in revenue
    assert "start_filter = moscow_date_boundary(date_from)" in revenue
    assert "end_filter = moscow_date_boundary(date_to) + timedelta(days=1)" in revenue


def test_admin_athlete_lists_use_collapsible_dropdown_blocks():
    admin = Path("templates/admin.html").read_text(encoding="utf-8")
    assert "athlete-dropdown" in admin
    assert admin.count("athlete-dropdown") >= 4
    assert "openChat(event," in admin


def test_webapp_create_student_has_keyboard_and_calendar_for_birthday():
    form = Path("templates/webapp_create_student.html").read_text(encoding="utf-8")
    assert 'id="birthday" type="text"' in form
    assert "replace(/\\D/g, '')" in form
    assert "showPicker" in form
    assert 'type = \'date\'' in form


def test_work_schedule_webapp_and_weekend_job_are_registered():
    settings = Path("handlers/admin_settings_panel.py").read_text(encoding="utf-8")
    webapp = Path("admin_module/webapp_views.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    template = Path("templates/admin_work_schedule.html").read_text(encoding="utf-8")
    jobs = Path("services/scheduler_jobs.py").read_text(encoding="utf-8")

    assert "admin-work-schedule" in settings
    assert "/webapp/admin-work-schedule" in webapp
    assert "work_schedule_changed" in webapp
    assert "webapp/admin-work-schedule/change" in template
    assert "work_schedule" in template
    assert "send_work_schedule_notice" in jobs
    assert "admin_schedulers" in settings
    assert "birthday_missing_reminders" in settings
    assert "subscription_expiry_reminders" in settings
    assert "birthday_greetings" in settings
    assert "absence_reminders" in settings
    assert "work_schedule_reminders" in settings
    assert "stock_reminders" in settings
    assert "work_schedule_sat" in main
    assert "work_schedule_sun" in main
    assert "work_schedule_mon" in main
    assert "stock_reminder_am" in main
    assert "stock_reminder_pm" in main


def test_subscription_forms_explain_unlimited_marker_999():
    bot = Path("templates/webapp_buy_subscription.html").read_text(encoding="utf-8")
    tariffs = Path("templates/admin_tariffs.html").read_text(encoding="utf-8")
    assert "999" in bot
    assert "999" in tariffs
    assert "createDiscipline" in tariffs
    assert "newDisciplineName" in tariffs
    assert "Копировать тарифы из дисциплины" in tariffs
    assert "copyFromDisc" in tariffs


def test_cash_register_and_stats_show_distinct_cashflow_terms():
    cash = Path("templates/cash_register.html").read_text(encoding="utf-8")
    stats = Path("templates/stats.html").read_text(encoding="utf-8")
    assert cash.index("Касса и финансовый журнал") < cash.index("Все операции")
    assert "Маржа" in cash
    assert 'name="date_from"' in cash
    assert 'name="date_to"' in cash
    assert "cash_income_total" in cash
    assert "cash_expenses_total" in cash
    assert "cash_income_total/100" not in cash
    assert "Расходы сегодня" in stats
    assert "Маржа сегодня" in stats
    assert "Расходы за неделю" in stats


def test_student_delete_endpoint_and_webapp_form_are_registered():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    page = Path("templates/admin_students.html").read_text(encoding="utf-8")
    assert '@router.delete("/admin/students/{student_id}")' in api
    assert "student_deleted" in api
    assert "danger-delete" in page


def test_admin_settings_menu_top_block_uses_readable_labels():
    panel = Path("handlers/admin_settings_panel.py").read_text(encoding="utf-8").splitlines()
    top_block = "\n".join(panel[29:70])
    assert "🛍 Настройки магазина / YooKassa" in top_block
    assert "График работы" in top_block
    assert "Касса и журнал" in top_block
    assert "Сайт клуба" in top_block
    assert "Поддержка" in top_block
    assert "⚙️ Настройка лимитов клуба" in top_block
    assert "⏰ Планировщики" in top_block
    assert "СКУД(Турникет)" in top_block
    assert "🥋 Управление секциями" in top_block
    assert "Уведомления по складу" not in top_block
    assert "вЂ" not in top_block
    assert "рџ" not in top_block


def test_admin_settings_submenus_use_readable_labels():
    panel = Path("handlers/admin_settings_panel.py").read_text(encoding="utf-8")
    assert "⏰ <b>Планировщики клуба</b>" in panel
    assert "Нет даты рождения" in panel
    assert "Окончание абонемента" in panel
    assert "Склад" in panel
    assert "🥋 <b>Список направлений</b>" in panel
    assert "📡 <b>Интеграция СКУД (Турникет)</b>" in panel
    assert "⚙️ <b>Управление лимитами клуба" in panel
    assert "🎨 Отправьте JSON одной строкой" in panel
    assert "вЂ" not in panel
    assert "рџ" not in panel


def test_webapp_loading_logo_prefers_loading_config_and_keeps_cache_buster():
    from admin_module.webapp_client_cabinet import _webapp_loading_config

    class ClubStub:
        def __init__(self, club_settings):
            self.club_settings = club_settings

    loading_first = _webapp_loading_config(
        ClubStub({"ui": {"logo_url": "/static/uploads/logos/ui.jpg", "loading": {"enabled": True, "logo_url": "/static/uploads/logos/loading.jpg", "logo_rev": "abc123"}}})
    )
    assert loading_first["logo_url"] == "/static/uploads/logos/loading.jpg"
    assert loading_first["logo_rev"] == "abc123"

    loading_fallback = _webapp_loading_config(
        ClubStub({"ui": {"logo_url": "/static/uploads/logos/ui.jpg", "loading": {"enabled": True}}})
    )
    assert loading_fallback["logo_url"] == "/static/uploads/logos/ui.jpg"
    assert loading_fallback["logo_rev"] == ""


def test_user_and_admin_history_render_database_utc_as_moscow_time():
    student = Path("templates/webapp_student.html").read_text(encoding="utf-8")
    history = Path("templates/webapp_history.html").read_text(encoding="utf-8")
    admin = Path("admin_module/admin_pages.py").read_text(encoding="utf-8")
    bot = Path("handlers/user_option.py").read_text(encoding="utf-8")
    assert "group_completed_sessions" in bot
    assert "summarize_payment_entry" in bot
    assert "display_last_visit = last_visit_naive + timedelta(hours=3)" in admin
    assert "visit_sessions" in student
    assert "visit_sessions" in history
    assert "payment_lines" in history


def test_background_scheduler_has_stable_moscow_schedule_and_no_overlap():
    source = Path("main.py").read_text(encoding="utf-8")
    assert 'AsyncIOScheduler(timezone=ZoneInfo("Europe/Moscow"))' in source
    for job_id in ("daily_morning_notifications", "expiring_pass_notifications", "daily_admin_report", "auto_close_sessions", "daily_database_backup"):
        assert f'id="{job_id}"' in source
    assert "max_instances=1" in source
    assert "scheduler.shutdown(wait=False)" in source


@pytest.mark.asyncio
async def test_cart_checkout_ensures_webapp_user_exists_before_order_insert():
    from admin_module.api import _ensure_cart_user

    created = []

    class FakeUser:
        def __init__(self, user_id, club_id=None, full_name=None, is_accepted=False, is_biometric_enabled=False):
            self.user_id = user_id
            self.club_id = club_id
            self.full_name = full_name
            self.is_accepted = is_accepted
            self.is_biometric_enabled = is_biometric_enabled

    class FakeSession:
        def __init__(self):
            self.stored = None

        async def get(self, model, user_id, with_for_update=False):
            return None

        def add(self, obj):
            created.append(obj)
            self.stored = obj

        async def flush(self):
            return None

    club = type("Club", (), {"id": 2})()
    tg_user = {"id": 5898364782, "first_name": "Ivan", "last_name": "Petrov"}

    session = FakeSession()
    user = await _ensure_cart_user(session, club, tg_user)

    assert user.user_id == 5898364782
    assert user.club_id == 2
    assert user.full_name == "Ivan"
    assert created and created[0] is user

    existing = FakeUser(user_id=5898364782, club_id=1, full_name="")

    class ExistingSession(FakeSession):
        async def get(self, model, user_id, with_for_update=False):
            return existing

    session2 = ExistingSession()
    user2 = await _ensure_cart_user(session2, club, tg_user)

    assert user2 is existing
    assert existing.club_id == 1
    assert existing.full_name == "Ivan"


def test_admin_audit_screen_and_button_are_registered():
    keys = route_keys()
    assert ("/webapp/admin-audit", "GET") in keys
    buttons = Path("handlers/buttons.py").read_text(encoding="utf-8")
    page = Path("templates/admin_audit.html").read_text(encoding="utf-8")
    assert "📜 Аудит" in buttons
    assert "actor_role" in page
    assert "Показываются последние 300 записей" in page
    assert "closeWebApp" in page


def test_club_settings_reads_are_guarded_and_super_admin_errors_use_logger():
    main = Path("main.py").read_text(encoding="utf-8")
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    payments = Path("handlers/payments.py").read_text(encoding="utf-8")
    super_handlers = Path("handlers/super_admin_handlers.py").read_text(encoding="utf-8")
    assert "(club.club_settings or {}).get(\"payments\", {})" in main
    scheduler_jobs = Path("services/scheduler_jobs.py").read_text(encoding="utf-8")
    assert "(club.club_settings or {}).get(\"disciplines\", {})" in scheduler_jobs
    assert "(club.club_settings or {}).get(\"disciplines\", {})" in api
    assert "(club.club_settings or {}).get(\"disciplines\", {})" in payments
    assert "(club.club_settings or {}).get(\"payments\", {})" in payments
    assert "print(f\"Ошибка в хендлере extend_club_sub: {e}\")" not in super_handlers
    assert "logger.exception(\"Ошибка в хендлере extend_club_sub\")" in super_handlers


def test_client_cabinet_bottom_bar_is_collapsible_and_uses_high_contrast_actions():
    page = Path("templates/client_cabinet.html").read_text(encoding="utf-8")
    assert "id=\"bottomBar\"" in page
    assert "toggleBottomBar" in page
    assert "Свернуть быстрые действия" in page
    assert "Развернуть быстрые действия" in page
    assert ".bottom-bar.collapsed .inner" in page
    assert ".bottom-bar .btn.soft" in page
    assert ".bottom-bar .btn.dark" in page


def test_schedule_webapp_bottom_bar_is_collapsible():
    page = Path("templates/schedule.html").read_text(encoding="utf-8")
    assert "toggleBottomBar" in page
    assert ".bottom-bar.collapsed .inner" in page
    assert "Свернуть быстрые действия" in page
    assert "Развернуть быстрые действия" in page
    assert "discipline-title span:last-child" in page
    assert "color: #f7f7f7 !important" in page


def test_admin_schedule_webapp_supports_copy_from_day_and_dark_contrast():
    page = Path("templates/admin_schedule.html").read_text(encoding="utf-8")
    api = Path("admin_module/webapp_views.py").read_text(encoding="utf-8")
    assert "copy_day" in page
    assert "copy_from" in page
    assert "copyFrom" in page
    assert "copyFromDisc" in page
    assert "selectionMeta" in page
    assert "Копировать занятия из дня" in page
    assert "Копировать расписание из дисциплины" in page
    assert "source_day" in api
    assert "source_discipline" in api
    assert "Нельзя копировать день в сам себя" in api
    assert "Нельзя копировать дисциплину в саму себя" in api
    assert "В исходном дне нет занятий" in api
    assert "В исходной дисциплине нет занятий" in api


@pytest.mark.asyncio
async def test_admin_schedule_copy_day_duplicates_existing_lessons(monkeypatch):
    from admin_module import webapp_views
    from admin_module.api import ScheduleChangePayload

    club = type(
        "Club",
        (),
        {
            "id": 7,
            "club_settings": {
                "disciplines": {
                    "boxing": {
                        "name": "🥊 Бокс",
                        "schedule": {
                            "mon": [{"time": "10:00", "coach": "Тренер А", "max_slots": 12}],
                            "tue": [],
                        },
                    }
                }
            },
        },
    )()

    class FakeResult:
        def scalar_one_or_none(self):
            return club

    class FakeSession:
        def __init__(self):
            self.committed = False

        async def execute(self, *args, **kwargs):
            return FakeResult()

        async def commit(self):
            self.committed = True

    captured = {}

    async def fake_actor_context(*args, **kwargs):
        return {}

    def fake_audit_event(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs

    async def fake_verify(*args, **kwargs):
        return {"id": 1001}

    monkeypatch.setattr(webapp_views, "verify_webapp_staff", fake_verify)
    monkeypatch.setattr(webapp_views, "audit_actor_context", fake_actor_context)
    monkeypatch.setattr(webapp_views, "audit_event", fake_audit_event)

    payload = ScheduleChangePayload(
        init_data="init",
        club_id=7,
        action="copy_day",
        discipline="boxing",
        day="tue",
        source_day="mon",
    )

    result = await webapp_views.change_admin_schedule(payload, session=FakeSession())

    assert result == {"success": True}
    assert club.club_settings["disciplines"]["boxing"]["schedule"]["tue"] == [
        {"time": "10:00", "coach": "Тренер А", "max_slots": 12, "taken_slots": 0}
    ]
    assert captured["name"] == "schedule_changed"
    assert captured["kwargs"]["source_day"] == "mon"
    assert captured["kwargs"]["day"] == "tue"


@pytest.mark.asyncio
async def test_admin_schedule_copy_from_discipline_duplicates_entire_schedule(monkeypatch):
    from admin_module import webapp_views
    from admin_module.api import ScheduleChangePayload

    club = type(
        "Club",
        (object,),
        {
            "id": 7,
            "club_settings": {
                "disciplines": {
                    "jiu_jitsu": {
                        "name": "Джиу-джитсу",
                        "schedule": {
                            "mon": [{"time": "10:00", "coach": "Тренер А", "max_slots": 12}],
                            "tue": [{"time": "12:00", "coach": "Тренер Б", "max_slots": 8}],
                        },
                    },
                    "grappling": {
                        "name": "Грэпплинг",
                        "schedule": {
                            "mon": [],
                            "tue": [],
                        },
                    },
                }
            },
        },
    )()

    class FakeResult:
        def scalar_one_or_none(self):
            return club

    class FakeSession:
        def __init__(self):
            self.committed = False

        async def execute(self, *args, **kwargs):
            return FakeResult()

        async def commit(self):
            self.committed = True

        def add(self, obj):
            self.added = obj

    captured = {}

    async def fake_actor_context(*args, **kwargs):
        return {}

    def fake_audit_event(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs

    async def fake_verify(*args, **kwargs):
        return {"id": 1001}

    monkeypatch.setattr(webapp_views, "verify_webapp_staff", fake_verify)
    monkeypatch.setattr(webapp_views, "audit_actor_context", fake_actor_context)
    monkeypatch.setattr(webapp_views, "audit_event", fake_audit_event)

    payload = ScheduleChangePayload(
        init_data="init",
        club_id=7,
        action="copy_from",
        discipline="grappling",
        day="mon",
        source_discipline="jiu_jitsu",
    )

    result = await webapp_views.change_admin_schedule(payload, session=FakeSession())

    assert result == {"success": True}
    assert club.club_settings["disciplines"]["grappling"]["schedule"]["mon"] == [
        {"time": "10:00", "coach": "Тренер А", "max_slots": 12, "taken_slots": 0},
    ]
    assert club.club_settings["disciplines"]["grappling"]["schedule"]["tue"] == [
        {"time": "12:00", "coach": "Тренер Б", "max_slots": 8, "taken_slots": 0},
    ]
    assert captured["name"] == "schedule_copied"
    assert captured["kwargs"]["source_discipline"] == "jiu_jitsu"
    assert captured["kwargs"]["discipline"] == "grappling"


def test_master_bot_registers_new_club_bot_without_restart():
    super_source = Path("handlers/super_admin_handlers.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    config = Path("config.py").read_text(encoding="utf-8")
    registry = Path("services/bot_registry.py").read_text(encoding="utf-8")
    assert "register_bot" in super_source
    assert "BASE_URL" in super_source
    assert "register_existing_bots" in main
    assert "close_all_bots" in main
    assert "BASE_URL" in config
    assert "os.getenv('BASE_URL', 'https://speedycrm.ru')" in config
    assert "bots_dict: dict[str, Bot] = {}" in registry
    assert "async def register_bot" in registry


def test_reload_cache_merges_only_missing_fields_and_keeps_manual_settings():
    current = {
        "ui": {"club_name": "Мой клуб", "support_enabled": False},
        "limits": {"session_timeout_minutes": 777},
        "features": {"freeze": False},
    }
    merged, changed = merge_default_club_settings(current, DEFAULT_CLUB_SETTINGS)
    assert changed is True
    assert merged["ui"]["club_name"] == "Мой клуб"
    assert merged["ui"]["support_enabled"] is False
    assert merged["limits"]["session_timeout_minutes"] == 777
    assert merged["features"]["freeze"] is False
    assert "payments" in merged
    assert "disciplines" in merged
    assert merged is not current


def test_daily_student_birthday_reminder_is_scheduled_and_uses_student_flow():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "scheduler.add_job(saas_daily_morning_check" in source
    assert "missing_birthdays" in source
    assert "callback_data=\"edit_birthday\"" in source
    assert "Student.birthday" not in source  # reminder reads Student instances, no parent birthday field is required


def test_admin_can_view_and_update_parent_phone_and_mailing_is_sequenced():
    schema = Path("admin_module/schemas.py").read_text(encoding="utf-8")
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    page = Path("templates/admin_students.html").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert "parent_phone: str | None = None" in schema
    assert "payload.parent_phone" in api and "normalize_ru_phone" in api
    assert 'name="parent_phone"' in page
    assert "data-filter=\"noParent\"" in page
    assert "hour=10, minute=5" in main
    assert "requestFullscreen" in page


def test_admin_create_student_ui_has_complete_submit_flow():
    page = Path("templates/admin_students.html").read_text(encoding="utf-8")
    assert 'id="saveCreate"' in page
    assert "document.getElementById('saveCreate').onclick = createStudent" in page
    assert "fetch('/admin/students'" in page
    assert "init_data: initData" in page
    assert "list.insertAdjacentHTML('afterbegin', studentCardHtml(s))" in page
    assert "bindEdit(newForm)" in page
    assert "new URLSearchParams(window.location.search).get('init_data')" in page


def test_admin_student_update_requires_club_scope():
    with pytest.raises(ValidationError):
        AdminStudentUpdate(init_data="signed", balance_lessons=10)


def test_bot_manual_add_button_has_registered_flow_and_no_orphan_states():
    buttons = Path("handlers/buttons.py").read_text(encoding="utf-8")
    handler = Path("handlers/admin_students.py").read_text(encoding="utf-8")
    states = Path("handlers/states.py").read_text(encoding="utf-8")
    assert 'callback_data="admin_add_manual"' in buttons
    assert '@router.callback_query(F.data == "admin_add_manual")' in handler
    assert "AdminManualAdd.waiting_for_name" in handler
    assert "AdminManualAdd.waiting_for_phone" in handler
    assert "AdminManualAdd.waiting_for_discipline" in handler
    assert "AdminManualAdd.waiting_for_tariff" in handler
    assert "admin_manual_no_sub_" in handler
    assert "waiting_for_lessons" not in states
    assert "waiting_for_parent_id" not in states


def test_ru_phone_and_date_inputs_are_normalized():
    assert normalize_ru_phone("8 (999) 111-22-33") == "79991112233"
    assert normalize_ru_phone("+7 999 111 22 33") == "79991112233"
    assert normalize_ru_phone("9991112233") == "79991112233"
    assert parse_user_date("15.08.2012").isoformat() == "2012-08-15"
    assert parse_user_date("15082012").isoformat() == "2012-08-15"
    assert parse_user_date("15-08-2012").isoformat() == "2012-08-15"


def test_daily_report_has_no_manual_add_fragment():
    source = Path("handlers/admin_option.py").read_text(encoding="utf-8")
    assert "по тарифу {t_label}" not in source
    assert "sub_expire_str" not in source


def test_admin_student_update_accepts_valid_migration_fields():
    payload = AdminStudentUpdate(
        init_data="signed",
        club_id=7,
        birthday="2012-05-10",
        balance_lessons=999,
        expire_date="2026-12-31",
        can_freeze=1,
        is_frozen=0,
        frozen_days=None,
        discipline="boxing",
    )
    assert payload.club_id == 7
    assert payload.balance_lessons == 999


def test_legacy_student_create_requires_club_id():
    with pytest.raises(ValidationError):
        StudentCreate(name="Атлет", parent_id=10)


def test_legacy_student_create_contains_club_scope():
    payload = StudentCreate(name=" Атлет ", parent_id=10, club_id=7)
    assert payload.club_id == 7


def test_long_webapp_pages_include_scroll_to_top_button():
    admin = Path("templates/admin.html").read_text(encoding="utf-8")
    stats = Path("templates/stats.html").read_text(encoding="utf-8")
    shop = Path("templates/shop.html").read_text(encoding="utf-8")
    base = Path("templates/webapp_base.html").read_text(encoding="utf-8")
    script = Path("static/js/scroll_top.js").read_text(encoding="utf-8")
    assert "scroll_top.js" in admin
    assert "scroll_top.js" in stats
    assert "scroll_top.js" in shop
    assert "scroll_top.js" in base
    assert "scrollTopButton" in script


def test_club_isolation_contracts_cover_webapp_system_api_and_payments():
    webapp = Path("admin_module/webapp_views.py").read_text(encoding="utf-8")
    system_api = Path("admin_module/system_api.py").read_text(encoding="utf-8")
    payments = Path("handlers/payments.py").read_text(encoding="utf-8")

    assert "Student.parent_id == user_id, Student.club_id == club.id" in webapp
    assert "club_id: int = Query(...)" in system_api
    assert "select(User).where(User.club_id == club_id)" in system_api
    assert "select(Student).where(Student.id == student_id, Student.club_id == club_id)" in system_api
    assert "club_id = user.club_id or getattr(club" not in payments
    assert '"club_id": user.club_id' not in system_api
    assert "parent.club_id != data.club_id" not in system_api
