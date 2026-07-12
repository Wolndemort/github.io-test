import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone
from handlers.user_option import parse_qr_scan


def mock_sqlalchemy_result(student):
    """Вспомогательная функция для создания мока ответа execute() в SQLAlchemy 2.0"""
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = student
    return mock_res


@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature")
async def test_parse_qr_scan_new_session_success(mock_gen_sig):
    """Тест 1: Успешный проход с открытием НОВОЙ сессии. Занятие на входе НЕ должно списаться (за него отвечает крон)."""
    # 1. Настройка окружения
    mock_gen_sig.return_value = 'valid_sig'
    session = AsyncMock()

    club = MagicMock()
    club.id = 1
    club.name = "Test Club"

    club_settings = {
        "limits": {
            "session_timeout_minutes": 150,
            "freeze_days_step": 7
        },
        "turnstile": {"enabled": False}
    }

    # Синхронизируем генерацию времени с Московским часовым поясом, как в новом хендлере
    tz_moscow = timezone(timedelta(hours=3))
    now_local = datetime.now(tz_moscow).replace(tzinfo=None)

    # 2. Создаем студента, у которого прошлый визит был 5 часов назад
    class MockStudent:
        def __init__(self):
            self.id = 1
            self.name = 'Ivan'
            self.club_id = 1
            self.balance_lessons = 10
            self.is_frozen = 0
            self.last_visit = now_local - timedelta(hours=5)
            self.expire_date = now_local + timedelta(days=10)
            self.parent_id = 12345

    student = MockStudent()

    session.execute.return_value = mock_sqlalchemy_result(student)

    message = AsyncMock()
    message.web_app_data.data = "student:1:salt:valid_sig"
    message.answer = AsyncMock()
    message.bot.send_message = AsyncMock()

    # 3. Запуск функции
    await parse_qr_scan(message, session, club, club_settings)

    # 4. Проверки
    # ИСПРАВЛЕНО: Теперь на входе баланс НЕ списывается, он остается равен 10!
    assert student.balance_lessons == 10
    session.commit.assert_called_once()

    # Проверяем текст ответа бота
    args, kwargs = message.answer.call_args
    actual_text = args[0] if args else kwargs.get('text', '')
    assert "ПРОХОДИТЕ" in actual_text.upper()


@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature")
async def test_parse_qr_scan_inside_session_success(mock_gen_sig):
    """Тест 2: Повторный проход в рамках одной сессии (прошло 10 минут). Занятие НЕ должно списаться."""
    mock_gen_sig.return_value = 'valid_sig'
    session = AsyncMock()

    club = MagicMock()
    club.id = 1
    club.name = "Test Club"
    club_settings = {
        "limits": {"session_timeout_minutes": 150, "freeze_days_step": 7},
        "turnstile": {"enabled": False}
    }

    tz_moscow = timezone(timedelta(hours=3))
    now_local = datetime.now(tz_moscow).replace(tzinfo=None)

    # Создаем студента, у которого прошлый визит был всего 10 минут назад
    class MockStudent:
        def __init__(self):
            self.id = 1
            self.name = 'Ivan'
            self.club_id = 1
            self.balance_lessons = 10
            self.is_frozen = 0
            self.last_visit = now_local - timedelta(minutes=10)
            self.expire_date = now_local + timedelta(days=10)
            self.parent_id = 12345

    student = MockStudent()

    session.execute.return_value = mock_sqlalchemy_result(student)

    message = AsyncMock()
    message.web_app_data.data = "student:1:salt:valid_sig"
    message.answer = AsyncMock()
    message.bot.send_message = AsyncMock()

    # Запуск
    await parse_qr_scan(message, session, club, club_settings)

    # Проверки
    # Сессия активна -> баланс остался равен 10
    assert student.balance_lessons == 10
    session.commit.assert_called_once()

    # Проверяем, что бот пропустил атлета и вывел инфу про сессию
    args, kwargs = message.answer.call_args
    actual_text = args[0] if args else kwargs.get('text', '')
    assert "ПРОХОДИТЕ" in actual_text.upper()
    assert "ПОВТОРНЫЙ ПРОХОД" in actual_text.upper()
