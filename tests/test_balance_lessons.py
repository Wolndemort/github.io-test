from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from handlers.user_option import parse_qr_scan


def mock_sqlalchemy_result(student):
    """Вспомогательная функция для создания мока ответа execute() в SQLAlchemy 2.0"""
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = student
    return mock_res


@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature")
async def test_parse_qr_scan_no_lessons(mock_gen_sig):
    """Проверяем блокировку лимитного атлета при балансе 0, если сессия закрыта (визит 5 часов назад)"""
    mock_gen_sig.return_value = 'valid_sig'

    session = AsyncMock()
    club = MagicMock()
    club.id = 1
    club.name = "Test Club"

    # Задаем дефолтные лимиты в конфиг для теста
    club_settings = {
        "limits": {"session_timeout_minutes": 150, "freeze_days_step": 7},
        "turnstile": {"enabled": False}
    }

    student = MagicMock()
    student.name = 'AdamTest'
    student.club_id = 1
    student.balance_lessons = 0  # Лимитный абонемент закончился
    student.is_frozen = 0

    # Ставим визит 5 часов назад в UTC без таймзоны (как в реальной базе)
    student.last_visit = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)
    student.expire_date = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)

    # ИСПРАВЛЕНО ПОД NEW АРХИТЕКТУРУ: Мокаем execute() вместо get()
    session.execute.return_value = mock_sqlalchemy_result(student)

    message = AsyncMock()
    message.web_app_data.data = "student:1:salt:valid_sig"
    message.answer = AsyncMock()

    await parse_qr_scan(message, session, club, club_settings)

    args, kwargs = message.answer.call_args
    actual_text = args[0] if args else kwargs.get('text', '')

    assert "🔴 ДОСТУП ЗАПРЕЩЕН" in actual_text
    assert "❌ На балансе нет занятий" in actual_text
    assert student.balance_lessons == 0


@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature")
async def test_parse_qr_scan_unlimited_success(mock_gen_sig):
    """Проверяем, что безлимитного атлета (999) пускает в зал при открытии новой сессии"""
    mock_gen_sig.return_value = 'valid_sig'

    session = AsyncMock()
    club = MagicMock()
    club.id = 1
    club.name = "Test Club"
    club_settings = {
        "limits": {"session_timeout_minutes": 150, "freeze_days_step": 7},
        "turnstile": {"enabled": False}
    }

    student = MagicMock()
    student.name = 'AdamTest'
    student.club_id = 1
    student.balance_lessons = 999  # МАРКЕР БЕЗЛИМИТА
    student.is_frozen = 0

    # Ставим визит без таймзоны (naive), как требует новая архитектура на Аэзе
    student.last_visit = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)
    student.expire_date = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)

    # ИСПРАВЛЕНО ПОД NEW АРХИТЕКТУРУ: Мокаем execute() вместо get()
    session.execute.return_value = mock_sqlalchemy_result(student)

    message = AsyncMock()
    message.web_app_data.data = "student:1:salt:valid_sig"
    message.answer = AsyncMock()

    await parse_qr_scan(message, session, club, club_settings)

    args, kwargs = message.answer.call_args
    actual_text = args[0] if args else kwargs.get('text', '')

    assert "ПРОХОДИТЕ" in actual_text
    assert "♾ <b>Режим: Безлимит</b>" in actual_text
    assert student.balance_lessons == 999  # Баланс НЕ уменьшился!
