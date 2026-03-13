import pytest
from unittest.mock import AsyncMock,MagicMock,patch
from datetime import datetime, timedelta
from handlers.user_option import parse_qr_scan


@pytest.mark.asyncio
async def test_parse_qr_scan_invalid_format():
    message = AsyncMock()
    message.web_app_data.data = 'Invalid:format:data'
    message.answer = AsyncMock()

    await parse_qr_scan(message)
    message.answer.assert_called_once_with("❌ Ошибка: Неверный формат QR")


@pytest.mark.asyncio
@patch("handlers.user_option.AsyncSessionLocal")
@patch("handlers.user_option.generate_signature")
async def test_parse_qr_scan_success(mock_gen_sig, mock_session_class):

    mock_gen_sig.return_value = 'valid_sig'

    session = AsyncMock()
    mock_session_class.return_value.__aenter__.return_value = session

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

    await parse_qr_scan(message)

    assert student.balance_lessons == 9
    session.commit.assert_called_once()
    actual_text = message.answer.call_args[0][0]
    assert "ПРОХОДИТЕ" in actual_text.upper()
