import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from database.db import Student, Club, VisitLog  # Сверь пути импорта под свой проект

logger = logging.getLogger(__name__)


async def process_athlete_gate_pass(
    student_id: int,
    db,
    club_settings: dict,
    expected_club_id: int | None = None,
) -> dict:
    """
    Универсальный сервис контроля СКУД, сессий и разморозки.
    НЕ списывает занятия на входе (работает в тандеме с кроном).
    Возвращает словарь: {'success': bool, 'message': str, 'moscow_time_str': str}
    """
    club_settings = club_settings if isinstance(club_settings, dict) else {}

    # 1. ЗАЩИТА ROW-LEVEL LOCKING от Race Condition
    student_res = await db.execute(
        select(Student).where(Student.id == student_id).with_for_update()
    )
    student = student_res.scalar_one_or_none()
    if not student:
        return {"success": False, "message": "Атлет не найден в базе данных."}

    # QR/WebApp запрос должен работать только внутри клуба, из которого он пришёл.
    if expected_club_id is not None and student.club_id != expected_club_id:
        logger.warning(
            "Попытка прохода атлета из другого клуба: student=%s, club=%s, expected=%s",
            student_id,
            student.club_id,
            expected_club_id,
        )
        return {"success": False, "message": "Атлет не найден в этом клубе."}

    # Ищем клуб
    club_res = await db.execute(select(Club).where(Club.id == student.club_id))
    club = club_res.scalar_one_or_none()
    if not club:
        return {"success": False, "message": "Клуб атлета не найден."}

    # Строго наивное UTC-время сервера для Postgres
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    student_name = str(student.name)

    # 2. АНТИ-СПАМ (Защита от флуда тапов)
    # 2. АНТИ-СПАМ (Защита от флуда тапов)
    # ИСПРАВЛЕНО: Защита от дефолтных пустых значений у новых пользователей
    if student.last_visit is not None:
        last_visit_naive = student.last_visit.replace(tzinfo=None)

        # Если время визита из будущего или равно стартовой эпохе (баг инициализации БД), пропускаем
        if last_visit_naive.year > 2000:
            time_diff = (now_naive - last_visit_naive).total_seconds()

            # Срабатывает, только если реальный визит был в диапазоне от 0 до 10 секунд назад
            if 0 <= time_diff < 10:
                return {"success": False, "message": "⏳ Не спамьте, турникет уже обрабатывает запрос."}

    # 3. ЛОГИКА ДОСРОЧНОЙ РАЗМОРОЗКИ
    is_was_frozen = False
    returned_early_days = 0
    if student.is_frozen and student.frozen_at:
        frozen_at_naive = student.frozen_at.replace(tzinfo=None)
        days_passed = max(0, (now_naive.date() - frozen_at_naive.date()).days)
        # Для платной заморозки берем реально купленный срок; старые записи
        # без frozen_days используют стандартный шаг клуба.
        freeze_step = getattr(student, "frozen_days", None) or club_settings.get("limits", {}).get("freeze_days_step", 7)

        if days_passed < freeze_step:
            diff = freeze_step - days_passed
            if student.expire_date:
                student.expire_date -= timedelta(days=diff)
            returned_early_days = diff
            logger.info(f"❄️ СКУД Досрочный выход: {student_name} недогулял {diff} дн. Срок уменьшен.")

        student.is_frozen = 0
        student.frozen_at = None
        if hasattr(student, "frozen_days"):
            student.frozen_days = None
        is_was_frozen = True
        await db.flush()

    # 4. КОНТРОЛЬ СЕССИИ (Таймаут из JSONB)
    timeout_minutes = club_settings.get("limits", {}).get("session_timeout_minutes", 150)
    is_inside_session = False
    if student.last_visit:
        last_visit_naive = student.last_visit.replace(tzinfo=None)
        if timedelta(0) <= now_naive - last_visit_naive < timedelta(minutes=timeout_minutes):
            is_inside_session = True

    # 5. ПРОВЕРКА СРОКА ДЕЙСТВИЯ И БАЛАНСА
    expire_naive = student.expire_date.replace(tzinfo=None) if student.expire_date else None
    if expire_naive and expire_naive < now_naive:
        return {"success": False,
                "message": f"❌ Срок действия абонемента истек (До: {expire_naive.strftime('%d.%m.%Y')})"}

    balance = student.balance_lessons or 0
    is_unlimited = (balance == 999)
    if not is_unlimited and not is_inside_session and balance <= 0:
        return {"success": False, "message": "❌ На балансе нет доступных занятий."}

    # 6. УПРАВЛЕНИЕ СЕССИЕЙ (Без списания уроков!)
    if is_inside_session:
        visit_moscow = student.last_visit.replace(tzinfo=None) + timedelta(hours=3)
        session_end = visit_moscow + timedelta(minutes=timeout_minutes)
        session_end_str = session_end.strftime("%H:%M")
        message_text = f"Повторный проход. Сессия активна до {session_end_str}."
    else:
        student.last_visit = now_naive  # Открываем новую сессию в UTC
        balance_text = "Безлимит" if is_unlimited else f"{balance} зан."
        message_text = f"Приятной тренировки! Доступно: {balance_text}"

    db.add(VisitLog(
        student_id=student.id,
        club_id=student.club_id,
        visited_at=now_naive,
        source="gate" if not is_inside_session else "repeat"
    ))

    # 7. СНАЧАЛА ПОДТВЕРЖДАЕМ ОТКРЫТИЕ ЖЕЛЕЗА, НЕ МЕНЯЯ ЛОГИКУ СЕССИИ.
    # Если реле отказало, откатываем last_visit/VisitLog и позволяем повторить
    # попытку. При успешном открытии ниже будет тот же commit, что и раньше.
    relay_config = dict(club_settings.get("turnstile", {}) or {})
    turnstile_status = "ℹ️ СКУД отключен"
    if relay_config.get("enabled", False):
        try:
            base_url = str(relay_config.get("base_url", ""))
            if base_url and not base_url.startswith("http"):
                relay_config["base_url"] = f"http://{base_url}"

            # trigger_dingtian_turnstile должен быть импортирован или лежать в утилитах
            from handlers.skud import trigger_dingtian_turnstile  # Сверь путь импорта!
            is_opened = await trigger_dingtian_turnstile(relay_config)
            if not is_opened:
                await db.rollback()
                return {
                    "success": False,
                    "message": "⚠️ Реле турникета отклонило команду. Турникет сейчас недоступен: проход не записан, занятия не списаны. Сообщите администратору или попробуйте ещё раз.",
                }
            turnstile_status = "✅ Турникет открыт"
        except Exception as sku_err:
            await db.rollback()
            logger.warning(f"Микросбой сети турникета: {sku_err}. Проход отклонён без записи.")
            return {
                "success": False,
                "message": "⚠️ Ошибка связи с турникетом. Проход не записан, занятия не списаны. Сообщите администратору или попробуйте ещё раз.",
            }

    # 8. После успешного открытия фиксируем ровно те же изменения сессии,
    # что и раньше. При выключенном СКУД commit выполняется сразу.
    try:
        await db.commit()
    except Exception as db_err:
        logger.error(f"Ошибка коммита СКУД сервиса: {db_err}")
        await db.rollback()
        return {"success": False, "message": "Ошибка сохранения данных визита."}

    # Готовим дополнительные флаги для вывода красивых уведомлений в ТГ
    return {
        "success": True,
        "message": message_text,
        "turnstile_status": turnstile_status,
        "student_name": student_name,
        "parent_id": student.parent_id,
        "club_name": club.name,
        "expire_str": student.expire_date.strftime('%d.%m.%Y') if student.expire_date else "Не указано",
        "is_was_frozen": is_was_frozen,
        "returned_early_days": returned_early_days,
        "balance": balance,
        "is_inside_session": is_inside_session
    }
