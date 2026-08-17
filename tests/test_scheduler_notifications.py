from pathlib import Path

import pytest

from services.schedule_utils import normalize_schedule_block


ROOT = Path(__file__).parents[1]


def test_expiring_subscription_notifications_use_all_parent_links():
    source = (ROOT / "services" / "scheduler_jobs.py").read_text(encoding="utf-8")
    start = source.index("async def check_abon_mailing")
    block = source[start:source.index("async def send_daily_report_to_admins", start)]
    assert "get_student_parent_ids(student.id, session)" in block
    assert "for parent_id in parent_ids" in block
    assert "Ошибка отправки (Student ID {student.id})" in block
    assert "parent_ids = await get_student_parent_ids(student.id, session)" in block
    assert "for parent_id in parent_ids" in block
    assert "club_id=student.club_id" in block


def test_scheduler_has_all_required_client_notification_flows():
    source = (ROOT / "services" / "scheduler_jobs.py").read_text(encoding="utf-8")
    assert "notify:no-subscription" in source
    assert "notify:birthday-missing" in source
    assert "notify:birthday:" in source
    assert "notify:absent:" in source
    assert "days_absent >= 10" in source
    assert "birthday_missing_reminders" in source
    assert "subscription_expiry_reminders" in source
    assert "birthday_greetings" in source
    assert "absence_reminders" in source

    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "scheduler.add_job(saas_daily_morning_check, 'cron', hour=10, minute=0" in main_source
    assert "scheduler.add_job(check_abon_mailing, 'cron', hour=10, minute=5" in main_source
    assert "scheduler.add_job(send_daily_report_to_admins, 'cron', hour=22, minute=0" in main_source
    assert "scheduler.add_job(send_work_schedule_notice, 'cron', day_of_week='sat', hour=11, minute=0" in main_source
    assert "scheduler.add_job(send_work_schedule_notice, 'cron', day_of_week='sun', hour=11, minute=0" in main_source
    assert "scheduler.add_job(send_work_schedule_notice, 'cron', day_of_week='mon', hour=11, minute=0" in main_source
    assert "scheduler.add_job(send_stock_reminder_notice, 'cron', hour=10, minute=0, args=['am']" in main_source
    assert "scheduler.add_job(send_stock_reminder_notice, 'cron', hour=18, minute=0, args=['pm']" in main_source


def test_expiring_scheduler_requires_real_active_subscription():
    source = (ROOT / "database" / "db.py").read_text(encoding="utf-8")
    assert "Student.parent_id.is_not(None)" in source
    assert "Student.balance_lessons > 0" in source
    assert "Student.balance_lessons <= 2" in source
    assert "Student.expire_date <= three_days_limit" in source


def test_expiring_reminders_cover_days_and_lessons():
    source = (ROOT / "services" / "scheduler_jobs.py").read_text(encoding="utf-8")
    assert "_subscription_reminder_flags" in source
    assert "days_left in {3, 2, 1}" in source
    assert "lessons" in source
    assert "notify:expire:" in source


def test_work_schedule_scheduler_reads_config_and_formats_by_day_mode():
    source = (ROOT / "services" / "scheduler_jobs.py").read_text(encoding="utf-8")
    assert "settings.get(\"work_schedule\", {})" in source
    assert "club.club_settings if isinstance(club.club_settings, dict) else {}" in source
    assert "_format_work_schedule_notice" in source
    assert "if mode == \"sat\"" in source
    assert "if mode == \"sun\"" in source
    assert "else:" in source
    assert "sat" in source and "sun" in source and "mon" in source


def test_scheduled_reports_and_work_schedule_notifications_are_duplicate_safe():
    source = (ROOT / "services" / "scheduler_jobs.py").read_text(encoding="utf-8")
    assert "notify:daily-report:" in source
    assert "notify:work-schedule:" in source
    assert "if notification_key:" in source


def test_user_schedule_webapp_has_day_keys_and_client_auth():
    pages = (ROOT / "admin_module" / "admin_pages.py").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "schedule.html").read_text(encoding="utf-8")
    user_bot = (ROOT / "handlers" / "user_option.py").read_text(encoding="utf-8")
    assert "verify_telegram_data(init_data, club.bot_token)" in pages
    assert '"key": day_key' in pages
    assert "const days = {};" in template
    assert 'not discipline_cfg.get("active", True)' in user_bot


def test_schedule_normalizer_keeps_all_lessons_and_legacy_shapes():
    raw = {
        "mon": {"time": "10:00", "coach": "A", "max_slots": 10, "taken_slots": 2},
        "tue": [
            {"time": "11:00", "coach": "B", "max_slots": 12, "taken_slots": 3},
            {"time": "12:00", "coach": "C", "max_slots": 14, "taken_slots": 4},
        ],
    }
    normalized = normalize_schedule_block(raw)
    assert len(normalized["mon"]) == 1
    assert len(normalized["tue"]) == 2
    assert normalized["tue"][0]["time"] == "11:00"


def test_stock_reminder_scheduler_is_registered_in_main_and_uses_threshold():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "send_stock_reminder_notice" in source
    assert "stock_reminder_am" in source
    assert "stock_reminder_pm" in source

    scheduler_source = (ROOT / "services" / "scheduler_jobs.py").read_text(encoding="utf-8")
    assert "stock_reminders" in scheduler_source
    assert "ClubProduct.stock <= 3" in scheduler_source
    assert "_format_stock_reminder" in scheduler_source
    assert "notify_stock_reminders" in scheduler_source


@pytest.mark.asyncio
async def test_work_schedule_notice_uses_work_schedule_config_for_each_day(monkeypatch):
    from services import scheduler_jobs

    sent = []

    async def _always_true(*args, **kwargs):
        return True

    class FakeBot:
        async def send_message(self, chat_id, text, parse_mode=None):
            sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

        def scalar_one_or_none(self):
            return self._rows[0] if self._rows else None

    class FakeSession:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:
                club = type(
                    "Club",
                    (),
                    {
                        "id": 1,
                        "name": "Alpha",
                        "bot_token": "token-1",
                        "owner_id": 999,
                        "club_settings": {
                            "work_schedule": {
                                "sat": {"open": "10:00", "close": "14:00", "note": "short"},
                                "sun": {"open": "11:00", "close": "15:00"},
                                "mon": {"open": "09:00", "close": "18:00"},
                                "tue": {"open": "09:15", "close": "19:00"},
                                "wed": {"open": "09:15", "close": "19:00"},
                                "thu": {"open": "09:15", "close": "19:00"},
                                "fri": {"open": "09:15", "close": "19:00"},
                            }
                        },
                    },
                )()
                return FakeResult([club])
            return FakeResult([111, None, 222])

    class FakeCM:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(scheduler_jobs, "AsyncSessionLocal", lambda: FakeCM())
    monkeypatch.setattr(scheduler_jobs, "bots_dict", {"token-1": FakeBot()})
    monkeypatch.setattr(scheduler_jobs, "_notification_once", lambda *args, **kwargs: _always_true())

    await scheduler_jobs.send_work_schedule_notice("sat")
    await scheduler_jobs.send_work_schedule_notice("sun")
    await scheduler_jobs.send_work_schedule_notice("mon")

    assert len(sent) == 9
    assert sent[0]["chat_id"] == 999
    assert sent[1]["chat_id"] in {111, 222}
    assert "Alpha" in sent[0]["text"]
    assert "10:00" in sent[0]["text"]
    assert "11:00" in sent[3]["text"]
    assert "09:00" in sent[6]["text"]
    assert all(item["parse_mode"] == "HTML" for item in sent)
    assert all(item["chat_id"] in {111, 222, 999} for item in sent)


@pytest.mark.asyncio
async def test_work_schedule_notice_skips_missing_work_schedule(monkeypatch):
    from services import scheduler_jobs

    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, parse_mode=None):
            sent.append(chat_id)

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class FakeSession:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:
                club = type("Club", (), {"id": 1, "name": "Alpha", "bot_token": "token-1", "owner_id": 999, "club_settings": {}})()
                return FakeResult([club])
            return FakeResult([111])

    class FakeCM:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(scheduler_jobs, "AsyncSessionLocal", lambda: FakeCM())
    monkeypatch.setattr(scheduler_jobs, "bots_dict", {"token-1": FakeBot()})

    await scheduler_jobs.send_work_schedule_notice("sat")
    assert sent == []


@pytest.mark.asyncio
async def test_stock_reminder_notice_sends_to_owner_and_cashier_only_for_low_stock(monkeypatch):
    from services import scheduler_jobs

    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, parse_mode=None, disable_notification=None):
            sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "disable_notification": disable_notification})

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class FakeSession:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:
                club = type(
                    "Club",
                    (),
                    {
                        "id": 1,
                        "name": "Alpha",
                        "bot_token": "token-1",
                        "owner_id": 999,
                        "subscription_expire_at": scheduler_jobs.reporting_periods()["now"],
                        "club_settings": {"features": {"stock_reminders": True}},
                    },
                )()
                return FakeResult([club])
            return FakeResult([("Water", 3), ("Juice", 1)])

    class FakeCM:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(scheduler_jobs, "AsyncSessionLocal", lambda: FakeCM())
    monkeypatch.setattr(scheduler_jobs, "bots_dict", {"token-1": FakeBot()})

    async def fake_once(*args, **kwargs):
        return True

    monkeypatch.setattr(scheduler_jobs, "_notification_once", fake_once)

    captured = []

    async def fake_notify(bot, club, session, text):
        captured.append(text)

    monkeypatch.setattr(scheduler_jobs, "notify_stock_reminders", fake_notify)

    await scheduler_jobs.send_stock_reminder_notice("am")

    assert len(captured) == 1
    assert "Alpha" in captured[0]
    assert "Water" in captured[0] and "Juice" in captured[0]

