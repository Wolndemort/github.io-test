import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from handlers.user_option import parse_qr_scan


@pytest.mark.asyncio
@patch("handlers.user_option.AsyncSessionLocal")
@patch("handlers.user_option.generate_signature")
async def test_parse_qr_scan_no_lessons(mock_gen_sig, mock_session_class):
    """Проверям блокировку при балансе 0"""
    mock_gen_sig.return_value = 'valid_sig'

    session = AsyncMock()
    mock_session_class.return_value.__aenter__.return_value = session

    student = MagicMock()
    student.name = 'AdamTest'
    student.balance_lessons = 0
    student.is_frozen = 0
    student.last_visit = datetime.now() - timedelta(minutes=10)
    student.expire_date = datetime.now() + timedelta(days=1)
    session.get = AsyncMock(return_value=student)

    message = AsyncMock()
    message.web_app_data.data = "student:1:salt:valid_sig"
    message.answer = AsyncMock()

    await parse_qr_scan(message)

    actual_text = message.answer.call_args[0][0]
    assert "🔴 ДОСТУП ЗАПРЕЩЕН" in actual_text
    assert "❌ Нет занятий" in actual_text
    assert student.balance_lessons == 0
    session.commit.assert_called()

