from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from handlers.user_option import parse_qr_scan


@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature")
async def test_parse_qr_scan_no_lessons(mock_gen_sig):
    """Проверяем блокировку при балансе 0"""
    mock_gen_sig.return_value = 'valid_sig'

    # 1. Мокаем сессию и КЛУБ (ОБЯЗАТЕЛЬНО С ID)
    session = AsyncMock()
    club = MagicMock()
    club.id = 1
    club.name = "Test Club"
    club_settings = {}

    # 2. Мокаем студента (ID КЛУБА ДОЛЖЕН СОВПАДАТЬ)
    student = MagicMock()
    student.name = 'AdamTest'
    student.club_id = 1  # СОВПАДАЕТ С club.id!
    student.balance_lessons = 0
    student.is_frozen = 0
    student.last_visit = datetime.now() - timedelta(minutes=10)
    student.expire_date = datetime.now() + timedelta(days=1)

    session.get.return_value = student

    message = AsyncMock()
    message.web_app_data.data = "student:1:salt:valid_sig"
    message.answer = AsyncMock()

    # 3. Вызываем функцию со всеми аргументами
    await parse_qr_scan(message, session, club, club_settings)

    # 4. Проверяем результат
    args, kwargs = message.answer.call_args
    actual_text = args[0] if args else kwargs.get('text', '')

    assert "🔴 ДОСТУП ЗАПРЕЩЕН" in actual_text
    assert "❌ Нет занятий" in actual_text
    assert student.balance_lessons == 0
