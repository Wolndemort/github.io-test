from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from handlers.user_option import parse_qr_scan

@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature")
async def test_parse_qr_scan_no_lessons(mock_gen_sig):
    """Проверяем блокировку лимитного атлета при балансе 0"""
    mock_gen_sig.return_value = 'valid_sig'

    session = AsyncMock()
    club = MagicMock()
    club.id = 1
    club.name = "Test Club"
    club_settings = {"turnstile": {"enabled": False}} # Подстраховка для конфига

    student = MagicMock()
    student.name = 'AdamTest'
    student.club_id = 1
    student.balance_lessons = 0 # Лимитный абонемент закончился
    student.is_frozen = 0
    student.last_visit = datetime.now() - timedelta(minutes=10)
    student.expire_date = datetime.now() + timedelta(days=1)

    session.get.return_value = student

    message = AsyncMock()
    message.web_app_data.data = "student:1:salt:valid_sig"
    message.answer = AsyncMock()

    await parse_qr_scan(message, session, club, club_settings)

    args, kwargs = message.answer.call_args
    actual_text = args[0] if args else kwargs.get('text', '')

    # ИСПРАВЛЕНО под наш новый текст ошибки в parse_qr_scan
    assert "🔴 ДОСТУП ЗАПРЕЩЕН" in actual_text
    assert "❌ На балансе нет занятий" in actual_text
    assert student.balance_lessons == 0


@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature")
async def test_parse_qr_scan_unlimited_success(mock_gen_sig):
    """Проверяем, что безлимитного атлета (999) пускает в зал и баланс не списывается"""
    mock_gen_sig.return_value = 'valid_sig'

    session = AsyncMock()
    club = MagicMock()
    club.id = 1
    club.name = "Test Club"
    club_settings = {"turnstile": {"enabled": False}}

    student = MagicMock()
    student.name = 'AdamTest'
    student.club_id = 1
    student.balance_lessons = 999 # МАРКЕР БЕЗЛИМИТА
    student.is_frozen = 0
    student.last_visit = datetime.now() - timedelta(minutes=10)
    student.expire_date = datetime.now() + timedelta(days=30) # Срок активен

    session.get.return_value = student

    message = AsyncMock()
    message.web_app_data.data = "student:1:salt:valid_sig"
    message.answer = AsyncMock()

    await parse_qr_scan(message, session, club, club_settings)

    args, kwargs = message.answer.call_args
    actual_text = args[0] if args else kwargs.get('text', '')

    # Проверяем, что безлимитчика пустило и текст корректный
    assert "ПРОХОДИТЕ" in actual_text
    assert "♾ Режим: Безлимит" in actual_text
    assert student.balance_lessons == 999 # Баланс НЕ уменьшился!
