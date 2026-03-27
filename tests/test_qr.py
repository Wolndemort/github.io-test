import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from handlers.user_option import parse_qr_scan


@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature")
async def test_parse_qr_scan_success(mock_gen_sig):
    # 1. Настройка окружения
    mock_gen_sig.return_value = 'valid_sig'
    session = AsyncMock()

    # Создаем мок клуба и ЗАДАЕМ ЕМУ ID
    club = MagicMock()
    club.id = 1
    club.name = "Test Club"
    club_settings = {}

    # 2. Создаем студента КОРРЕКТНО
    # Используем PropertyMock или просто объект, чтобы математика -= 1 сработала
    class MockStudent:
        def __init__(self):
            self.id = 1
            self.name = 'Ivan'
            self.club_id = 1  # ДОЛЖЕН СОВПАДАТЬ С club.id ВЫШЕ!
            self.balance_lessons = 10
            self.is_frozen = 0
            self.last_visit = datetime.now() - timedelta(minutes=10)
            self.expire_date = datetime.now() + timedelta(days=10)
            self.parent_id = 12345

    student = MockStudent()

    # Имитируем получение этого конкретного студента из БД по ID
    # В хендлере: student = await session.get(Student, scanned_id)
    session.get.return_value = student

    message = AsyncMock()
    # Данные соответствуют формату: student:ID:SALT:SIGNATURE
    message.web_app_data.data = "student:1:salt:valid_sig"
    message.answer = AsyncMock()
    message.bot.send_message = AsyncMock()

    # 3. Запуск функции
    await parse_qr_scan(message, session, club, club_settings)

    # 4. Проверки
    # Теперь баланс точно уменьшится, так как student — это объект с атрибутом
    assert student.balance_lessons == 9
    session.commit.assert_called_once()

    # Проверяем, что ответ был именно "ПРОХОДИТЕ"
    # Достаем текст из вызова .answer()
    args, kwargs = message.answer.call_args
    actual_text = args[0] if args else kwargs.get('text', '')
    assert "ПРОХОДИТЕ" in actual_text.upper()
