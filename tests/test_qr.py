import pytest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from handlers.user_option import parse_qr_scan


class FakeResult:
    def __init__(self, primary=None, extra=None):
        self._primary = primary
        self._extra = extra or []

    def scalar_one_or_none(self):
        return self._primary

    def scalars(self):
        return self

    def all(self):
        return list(self._extra)


class FakeSession:
    def __init__(self):
        self.result = FakeResult()

    async def execute(self, _stmt):
        return self.result


@pytest.mark.asyncio
@patch("handlers.user_option.get_student_parent_ids", new=AsyncMock(return_value=[12345]))
@patch("handlers.user_option.generate_signature")
@patch("handlers.user_option.process_athlete_gate_pass")
async def test_parse_qr_scan_new_session_success(mock_gate_service, mock_gen_sig):
    """Тест 1: Успешный проход через QR-сканер с вызовом центрального сервиса прохода."""
    # 1. Настройка заглушек (моков)
    mock_gen_sig.return_value = 'valid_sig'

    # Мокаем успешный ответ от нашего нового единого сервиса СКУД
    mock_gate_service.return_value = {
        "success": True,
        "message": "Приятной тренировки! Доступно: 10 зан.",
        "turnstile_status": "✅ Турникет открыт",
        "student_name": "Ivan",
        "parent_id": 12345,
        "club_name": "Test Club",
        "expire_str": "12.12.2026",
        "is_was_frozen": False,
        "returned_early_days": 0,
        "balance": 10,
        "is_inside_session": False
    }

    session = FakeSession()
    club = AsyncMock()
    club.id = 1
    club.name = "Test Club"
    club_settings = {"limits": {"session_timeout_minutes": 150}}
    redis = AsyncMock()

    message = AsyncMock()
    message.web_app_data.data = f"student:1:{datetime.now(timezone.utc):%Y-%m-%d-%H}:valid_sig"
    message.answer = AsyncMock()
    message.bot = SimpleNamespace(send_message=AsyncMock())

    # 2. Запуск хендлера
    await parse_qr_scan(message, session, club, club_settings, redis)

    # 3. Проверки
    # Проверяем, что хендлер честно вызвал наш центральный сервис с правильным id ученика
    mock_gate_service.assert_called_once_with(
        1, session, club_settings, expected_club_id=1, redis=redis
    )

    # Проверяем, что бот вывел админу/родителю сообщение об успешном проходе
    args, kwargs = message.answer.call_args
    actual_text = args[0] if args else kwargs.get('text', '')
    assert "ПРОХОДИТЕ" in actual_text.upper()


@pytest.mark.asyncio
@patch("handlers.user_option.get_student_parent_ids", new=AsyncMock(return_value=[12345]))
@patch("handlers.user_option.generate_signature")
@patch("handlers.user_option.process_athlete_gate_pass")
async def test_parse_qr_scan_inside_session_success(mock_gate_service, mock_gen_sig):
    """Тест 2: Повторный проход внутри сессии через QR-сканер."""
    mock_gen_sig.return_value = 'valid_sig'

    # Мокаем ответ сервиса для повторного визита в рамках сессии
    mock_gate_service.return_value = {
        "success": True,
        "message": "Повторный проход. Сессия активна до 20:00.",
        "turnstile_status": "✅ Турникет открыт",
        "student_name": "Ivan",
        "parent_id": 12345,
        "club_name": "Test Club",
        "expire_str": "12.12.2026",
        "is_was_frozen": False,
        "returned_early_days": 0,
        "balance": 10,
        "is_inside_session": True
    }

    session = FakeSession()
    club = AsyncMock()
    club.id = 1
    club.name = "Test Club"
    club_settings = {"limits": {"session_timeout_minutes": 150}}
    redis = AsyncMock()

    message = AsyncMock()
    message.web_app_data.data = f"student:1:{datetime.now(timezone.utc):%Y-%m-%d-%H}:valid_sig"
    message.answer = AsyncMock()
    message.bot = SimpleNamespace(send_message=AsyncMock())

    # Запуск
    await parse_qr_scan(message, session, club, club_settings, redis)

    # Проверки
    mock_gate_service.assert_called_once_with(
        1, session, club_settings, expected_club_id=1, redis=redis
    )

    args, kwargs = message.answer.call_args
    actual_text = args[0] if args else kwargs.get('text', '')
    assert "ПРОХОДИТЕ" in actual_text.upper()


@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature", return_value="expected_sig")
@patch("handlers.user_option.process_athlete_gate_pass")
async def test_parse_qr_scan_rejects_invalid_signature(mock_gate_service, _mock_gen_sig):
    session = FakeSession()
    club = AsyncMock()
    club.id = 1
    redis = AsyncMock()
    message = AsyncMock()
    message.web_app_data.data = f"student:1:{datetime.now(timezone.utc):%Y-%m-%d-%H}:forged_sig"
    message.answer = AsyncMock()

    await parse_qr_scan(message, session, club, {}, redis)

    mock_gate_service.assert_not_awaited()
    assert "Недействительный QR" in message.answer.await_args.args[0]


@pytest.mark.asyncio
@patch("handlers.user_option.generate_signature", return_value="expected_sig")
@patch("handlers.user_option.process_athlete_gate_pass")
async def test_parse_qr_scan_rejects_expired_qr(mock_gate_service, _mock_gen_sig):
    session = FakeSession()
    club = AsyncMock()
    club.id = 1
    redis = AsyncMock()
    message = AsyncMock()
    message.web_app_data.data = "student:1:2020-01-01-00:expected_sig"
    message.answer = AsyncMock()

    await parse_qr_scan(message, session, club, {}, redis)

    mock_gate_service.assert_not_awaited()
    assert "истёк" in message.answer.await_args.args[0]


@pytest.mark.asyncio
@patch("handlers.user_option.process_athlete_gate_pass")
async def test_parse_qr_scan_rejects_when_qr_feature_is_disabled(mock_gate_service):
    session = FakeSession()
    club = AsyncMock()
    club.id = 1
    redis = AsyncMock()
    message = AsyncMock()
    message.web_app_data.data = "student:1:2026-08-17-12:any"
    message.answer = AsyncMock()

    await parse_qr_scan(message, session, club, {"features": {"qr_checkin": False}}, redis)

    mock_gate_service.assert_not_awaited()
    assert "отключены" in message.answer.await_args.args[0]
