from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from handlers.user_option import parse_qr_scan


@pytest.mark.asyncio
# Убрали патч на AsyncSessionLocal, так как сессия теперь в аргументах
@patch("handlers.user_option.generate_signature")
async def test_parse_qr_scan_no_lessons(mock_gen_sig):  # Оставили только один мок
    """Проверяем блокировку при балансе 0"""
    mock_gen_sig.return_value = 'valid_sig'

    # 1. Мокаем зависимости SaaS (как в твоем middleware)
    session = AsyncMock()
    club = MagicMock()
    club.id = 1
    club_settings = {}  # Пустой JSONB конфиг

    # 2. Мокаем студента с нулевым балансом
    student = MagicMock()
    student.name = 'AdamTest'
    student.balance_lessons = 0
    student.is_frozen = 0
    student.last_visit = datetime.now() - timedelta(minutes=10)
    student.expire_date = datetime.now() + timedelta(days=1)

    # Настраиваем возврат студента из сессии
    session.get.return_value = student

    message = AsyncMock()
    message.web_app_data.data = "student:1:salt:valid_sig"
    message.answer = AsyncMock()

    # 3. Передаем ВСЕ 4 аргумента в функцию
    await parse_qr_scan(message, session, club, club_settings)

    # 4. Проверки
    # Вытаскиваем текст ответа из мока
    actual_text = message.answer.call_args[0][0]

    assert "🔴 ДОСТУП ЗАПРЕЩЕН" in actual_text
    assert "❌ Нет занятий" in actual_text
    assert student.balance_lessons == 0  # Баланс не должен уйти в минус
    session.commit.assert_called()  # Или assert_not_called(), если ты не коммитишь отказ
