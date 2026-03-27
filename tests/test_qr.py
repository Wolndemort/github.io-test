import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from handlers.user_option import parse_qr_scan


@pytest.mark.asyncio
async def test_parse_qr_scan_success():
    # 1. Готовим моки для новых аргументов
    session = AsyncMock()
    club = MagicMock()  # Твоя модель Club из SaaS
    club_settings = {"some": "settings"}  # Твой JSONB конфиг

    # 2. Мокаем студента (как и было)
    student = MagicMock()
    student.balance_lessons = 10
    # ... остальные поля студента ...
    session.get.return_value = student

    message = AsyncMock()
    message.web_app_data.data = "student:1:salt:valid_sig"

    # 3. Передаем всё в функцию (теперь без @patch внутри функции)
    with patch("handlers.user_option.generate_signature", return_value='valid_sig'):
        await parse_qr_scan(message, session, club, club_settings)

    # 4. Проверки
    assert student.balance_lessons == 9
    session.commit.assert_called_once()


@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature")
async def test_parse_qr_scan_success(mock_gen_sig):
    mock_gen_sig.return_value = 'valid_sig'

    # 1. Создаем моки для новых параметров SaaS
    session = AsyncMock()
    club = MagicMock()
    club.id = 1
    club_settings = {"some_option": True}

    # 2. Настраиваем возврат студента из базы
    student = MagicMock()
    student.name = 'Ivan'
    student.is_frozen = 0
    student.last_visit = datetime.now() - timedelta(minutes=10)
    student.expire_date = datetime.now() + timedelta(days=10)
    student.balance_lessons = 10
    student.parent_id = 12345
    session.get.return_value = student

    message = AsyncMock()
    message.web_app_data.data = "student:1:salt:valid_sig"
    message.answer = AsyncMock()
    message.bot.send_message = AsyncMock()

    # 3. ПЕРЕДАЕМ ВСЕ АРГУМЕНТЫ (как того требует новая сигнатура функции)
    await parse_qr_scan(message, session, club, club_settings)

    # 4. Проверки
    assert student.balance_lessons == 9
    session.commit.assert_called_once()
    actual_text = message.answer.call_args[0][0]
    assert "ПРОХОДИТЕ" in actual_text.upper()
