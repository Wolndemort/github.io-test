from pathlib import Path

import pytest

from services.schedule_utils import normalize_schedule_block


ROOT = Path(__file__).parents[1]


def test_scheduler_has_all_required_client_notification_flows():
    source = (ROOT / "services" / "scheduler_jobs.py").read_text(encoding="utf-8")
    assert "notify:no-subscription" in source
    assert "notify:birthday-missing" in source
    assert "notify:birthday:" in source
    assert "notify:absent:" in source
    assert "days_absent >= 10" in source
    assert "Указать дату рождения" in source
    assert "Выбрать абонемент" in source

    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "scheduler.add_job(saas_daily_morning_check, 'cron', hour=10, minute=0" in main_source
    assert "scheduler.add_job(check_abon_mailing, 'cron', hour=10, minute=5" in main_source
    assert "scheduler.add_job(send_daily_report_to_admins, 'cron', hour=22, minute=0" in main_source
    assert "scheduler.add_job(send_work_schedule_notice, 'cron', day_of_week='sat', hour=11, minute=0" in main_source
    assert "scheduler.add_job(send_work_schedule_notice, 'cron', day_of_week='sun', hour=11, minute=0" in main_source
    assert "scheduler.add_job(send_work_schedule_notice, 'cron', day_of_week='mon', hour=11, minute=0" in main_source


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
    assert "Осталось занятий" in source
    assert "notify:expire:" in source


def test_work_schedule_scheduler_reads_config_and_formats_by_day_mode():
    source = (ROOT / "services" / "scheduler_jobs.py").read_text(encoding="utf-8")
    assert "settings.get(\"work_schedule\", {})" in source
    assert "club.club_settings if isinstance(club.club_settings, dict) else {}" in source
    assert "_format_work_schedule_notice" in source
    assert "if mode == \"sat\"" in source
    assert "if mode == \"sun\"" in source
    assert "else:" in source
    assert "Наш клуб работает в субботу по следующему графику:" in source
    assert "Наш клуб работает в воскресенье по следующему графику:" in source
    assert "Наш клуб работает в понедельник по следующему графику:" in source
    assert "График занятий можете посмотреть во вкладке «Расписание»." in source


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


@pytest.mark.asyncio
async def test_work_schedule_notice_uses_work_schedule_config_for_each_day(monkeypatch):
    from services import scheduler_jobs

    sent = []

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

    await scheduler_jobs.send_work_schedule_notice("sat")
    await scheduler_jobs.send_work_schedule_notice("sun")
    await scheduler_jobs.send_work_schedule_notice("mon")

    assert len(sent) == 9
    assert sent[0]["chat_id"] == 999
    assert sent[1]["chat_id"] in {111, 222}
    assert "Наш клуб работает в субботу по следующему графику:" in sent[0]["text"]
    assert "График занятий можете посмотреть во вкладке «Расписание»." in sent[0]["text"]
    assert "Сб: <b>10:00–14:00</b> · short" in sent[0]["text"]
    assert "Наш клуб работает в воскресенье по следующему графику:" in sent[3]["text"]
    assert "Вс: <b>11:00–15:00</b>" in sent[3]["text"]
    assert "Наш клуб работает в понедельник по следующему графику:" in sent[6]["text"]
    assert "Пн: <b>09:00–18:00</b>" in sent[6]["text"]
    assert "Вт: <b>09:15–19:00</b>" in sent[6]["text"]
    assert "Пт: <b>09:15–19:00</b>" in sent[6]["text"]
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
