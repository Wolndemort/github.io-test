import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from handlers.user_option import parse_qr_scan


@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature")
@patch("handlers.user_option.process_athlete_gate_pass")
async def test_parse_qr_scan_no_lessons(mock_gate_service, mock_gen_sig):
    """Тест: Проверка поведения при отсутствии доступных занятий."""
    mock_gen_sig.return_value = 'valid_sig'

    # Имитируем, что центральный сервис отклонил проход из-за нулевого баланса
    mock_gate_service.return_value = {
        "success": False,
        "message": "❌ На балансе нет доступных занятий."
    }

    session = AsyncMock()
    club = AsyncMock()
    club_settings = {"limits": {"session_timeout_minutes": 150}}

    message = AsyncMock()
    message.web_app_data.data = f"student:1:{datetime.now(timezone.utc):%Y-%m-%d-%H}:valid_sig"
    message.answer = AsyncMock()

    # Запуск
    await parse_qr_scan(message, session, club, club_settings)

    # Проверяем, что хендлер отдал админу/пользователю ошибку, которую вернул сервис
    args, kwargs = message.answer.call_args
    actual_text = args[0] if args else kwargs.get('text', '')
    assert "НА БАЛАНСЕ НЕТ ДОСТУПНЫХ ЗАНЯТИЙ" in actual_text.upper()


@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature")
@patch("handlers.user_option.process_athlete_gate_pass")
async def test_parse_qr_scan_unlimited_success(mock_gate_service, mock_gen_sig):
    """Тест: Успешный проход атлета с безлимитным абонементом (маркер 999)."""
    mock_gen_sig.return_value = 'valid_sig'

    # Имитируем успешный ответ сервиса для безлимитчика
    mock_gate_service.return_value = {
        "success": True,
        "message": "Приятной тренировки! Доступно: Безлимит",
        "turnstile_status": "✅ Турникет открыт",
        "student_name": "AdamTest",
        "parent_id": 12345,
        "club_name": "Test Club",
        "expire_str": "12.08.2026",
        "is_was_frozen": False,
        "returned_early_days": 0,
        "balance": 999,
        "is_inside_session": False
    }

    session = AsyncMock()
    club = AsyncMock()
    club_settings = {"limits": {"session_timeout_minutes": 150}}

    message = AsyncMock()
    message.web_app_data.data = f"student:1:{datetime.now(timezone.utc):%Y-%m-%d-%H}:valid_sig"
    message.answer = AsyncMock()
    message.bot.send_message = AsyncMock()

    # Запуск
    await parse_qr_scan(message, session, club, club_settings)

    # Проверяем, что в ответе фигурирует успешный статус прохода
    args, kwargs = message.answer.call_args
    actual_text = args[0] if args else kwargs.get('text', '')
    assert "ПРОХОДИТЕ" in actual_text.upper()
