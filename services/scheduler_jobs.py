from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from html import escape

from aiogram import types
from loguru import logger
from sqlalchemy import select

from config import ADMIN_IDS
from database.db import (
    AsyncSessionLocal,
    CartOrder,
    Club,
    ClubProduct,
    ClubStaff,
    PaymentOrder,
    Student,
    StudentParent,
    User,
    VisitLog,
    create_db_backup,
    get_daily_stats,
    get_expire_students_grouped,
    get_student_parent_ids,
)
from handlers.buttons import get_profile_keyboard
from admin_module.utils import is_staff_or_owner
from services.order_notifications import notify_stock_reminders
from services.analytics import calculate_admin_dashboard, calculate_daily_business_report, reporting_periods
from services.bot_registry import bots_dict


async def expire_student_freezes():
    """Automatically finish freezes whose paid/free period has elapsed."""
    now = reporting_periods()["now"].replace(tzinfo=None)
    async with AsyncSessionLocal() as session:
        students = (await session.execute(
            select(Student).where(Student.is_frozen == 1, Student.frozen_at.is_not(None))
        )).scalars().all()
        for student in students:
            frozen_at = student.frozen_at.replace(tzinfo=None)
            freeze_days = int(student.frozen_days or 0)
            if not freeze_days or now < frozen_at + timedelta(days=freeze_days):
                continue
            student.is_frozen = 0
            student.frozen_at = None
            student.frozen_days = None
            club = await session.get(Club, student.club_id)
            bot = bots_dict.get(club.bot_token) if club else None
            if bot:
                text = f"❄️ <b>Заморозка завершена</b>\n\nАтлет: <b>{escape(student.name)}</b>\nАбонемент снова активен."
                for parent_id in await get_student_parent_ids(student.id, session):
                    try:
                        await bot.send_message(parent_id, text, parse_mode="HTML")
                    except Exception as exc:
                        logger.warning(f"Не удалось уведомить родителя {parent_id} о завершении заморозки: {exc}")
                if club.owner_id:
                    try:
                        await bot.send_message(club.owner_id, text, parse_mode="HTML")
                    except Exception as exc:
                        logger.warning(f"Не удалось уведомить владельца о завершении заморозки: {exc}")
        await session.commit()


async def send_backup_to_admin():
    """Создает бэкап всей БД и отправляет Супер-админам."""
    path = await create_db_backup()
    if not path or not os.path.exists(path):
        logger.error("❌ Файл бэкапа не был создан!")
        return

    random_bot = next(iter(bots_dict.values()), None)
    if not random_bot:
        logger.error("❌ Нет активных ботов для отправки бэкапа!")
        return

    for admin_id in ADMIN_IDS:
        try:
            await random_bot.send_document(
                chat_id=admin_id,
                document=types.FSInputFile(path),
                caption=f"📦 <b>SaaS Full Backup</b>\n📅 Дата: <code>{datetime.now().strftime('%d.%m.%Y')}</code>",
            )
            logger.info("✅ Бэкап отправлен супер-админу %s", admin_id)
        except Exception as exc:
            logger.error("❌ Ошибка отправки бэкапа админу %s: %s", admin_id, exc)

    if os.path.exists(path):
        os.remove(path)
        logger.debug("🗑️ Временный файл бэкапа удален с диска")


async def _notification_once(key: str, ttl: int = 86400) -> bool:
    """Protect scheduled Telegram notifications from duplicate sends."""
    try:
        from main import redis_client

        return bool(await redis_client.set(key, "1", ex=ttl, nx=True))
    except Exception as error:
        logger.warning("Не удалось проверить idempotency уведомления %s: %s", key, error)
        return True


async def _notification_forget(key: str) -> None:
    """Release an idempotency lock when delivery failed."""
    try:
        from main import redis_client

        await redis_client.delete(key)
    except Exception as error:
        logger.warning("Не удалось снять блокировку уведомления %s: %s", key, error)


def _subscription_reminder_flags(student, now_datetime: datetime) -> list[tuple[str, int]]:
    """Build reminder triggers for active subscriptions."""
    expire_date = getattr(student, "expire_date", None)
    balance = int(getattr(student, "balance_lessons", 0) or 0)
    flags: list[tuple[str, int]] = []

    if expire_date:
        expire_naive = expire_date.replace(tzinfo=None)
        days_left = (expire_naive.date() - now_datetime.date()).days
        if days_left in {3, 2, 1}:
            flags.append(("days", days_left))

    if balance and balance != 999 and balance <= 2:
        flags.append(("lessons", balance))

    return flags


def _format_work_schedule(club_name: str, work_schedule: dict) -> str:
    day_names = {"mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт", "fri": "Пт", "sat": "Сб", "sun": "Вс"}
    lines = [f"🕒 <b>График работы</b>", f"🏟 <b>{escape(club_name)}</b>", ""]
    for key in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
        row = work_schedule.get(key) or {}
        if row:
            open_at = escape(str(row.get("open", "—")))
            close_at = escape(str(row.get("close", "—")))
            note = escape(str(row.get("note", "")).strip())
            suffix = f" · {note}" if note else ""
            lines.append(f"{day_names[key]}: <b>{open_at}–{close_at}</b>{suffix}")
        else:
            lines.append(f"{day_names[key]}: <b>не задан</b>")
    lines.extend(["", "Проверьте график перед визитом."])
    return "\n".join(lines)


async def send_weekend_work_schedule():
    """Weekend reminder with club work hours for parents/clients."""
    now = reporting_periods()["now"]
    async with AsyncSessionLocal() as session:
        clubs = (await session.execute(select(Club).where(Club.subscription_expire_at >= now))).scalars().all()
        for club in clubs:
            settings = club.club_settings if isinstance(club.club_settings, dict) else {}
            work_schedule = settings.get("work_schedule", {})
            if not settings.get("features", {}).get("work_schedule_reminders", True):
                continue
            if not work_schedule or not club.bot_token:
                continue
            bot = bots_dict.get(club.bot_token)
            if not bot:
                continue
            recipients = set()
            parents_res = await session.execute(select(Student.parent_id).where(Student.club_id == club.id, Student.parent_id.isnot(None)))
            recipients.update(int(pid) for pid in parents_res.scalars().all() if pid)
            linked_res = await session.execute(
                select(StudentParent.parent_id).join(Student, StudentParent.student_id == Student.id).where(Student.club_id == club.id)
            )
            recipients.update(int(pid) for pid in linked_res.scalars().all() if pid)
            if club.owner_id:
                recipients.add(int(club.owner_id))
            if not recipients:
                continue
            text = _format_work_schedule(club.name or "Клуб", work_schedule)
            for chat_id in recipients:
                try:
                    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                    await asyncio.sleep(0.03)
                except Exception as exc:
                    logger.warning("Не удалось отправить график работы club=%s chat=%s: %s", club.id, chat_id, exc)


def _format_work_schedule_notice(club_name: str, work_schedule: dict, days: list[str], intro: str) -> str:
    day_names = {"mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт", "fri": "Пт", "sat": "Сб", "sun": "Вс"}
    lines = [intro, f"🏟 <b>{escape(club_name)}</b>", ""]
    for key in days:
        row = work_schedule.get(key) or {}
        if row:
            open_at = escape(str(row.get("open", "—")))
            close_at = escape(str(row.get("close", "—")))
            note = escape(str(row.get("note", "")).strip())
            suffix = f" · {note}" if note else ""
            lines.append(f"{day_names[key]}: <b>{open_at}–{close_at}</b>{suffix}")
        else:
            lines.append(f"{day_names[key]}: <b>не задан</b>")
    lines.extend(["", "Проверьте график перед визитом."])
    return "\n".join(lines)


def _format_stock_reminder(club_name: str, products: list[tuple[str, int]], *, bucket: str) -> str:
    intro = "Утреннее" if bucket == "am" else "Вечернее"
    lines = [
        f"📦 <b>{intro} напоминание по складу</b>",
        f"🏟 <b>{escape(club_name)}</b>",
        "",
        "Закупите товары, у которых остаток на уровне 3 и ниже:",
    ]
    for name, stock in products:
        lines.append(f"• {escape(name)} — <b>{stock}</b> шт.")
    lines.extend(["", "Проверьте склад и пополните остаток."])
    return "\n".join(lines)


async def send_stock_reminder_notice(bucket: str):
    now = reporting_periods()["now"]
    async with AsyncSessionLocal() as session:
        clubs = (await session.execute(select(Club).where(Club.subscription_expire_at >= now))).scalars().all()
        for club in clubs:
            settings = club.club_settings if isinstance(club.club_settings, dict) else {}
            if not settings.get("features", {}).get("stock_reminders", True):
                continue
            if not club.bot_token:
                continue
            bot = bots_dict.get(club.bot_token)
            if not bot:
                continue
            products = (
                await session.execute(
                    select(ClubProduct.name, ClubProduct.stock).where(
                        ClubProduct.club_id == club.id,
                        ClubProduct.stock <= 3,
                    ).order_by(ClubProduct.stock.asc(), ClubProduct.name.asc())
                )
            ).all()
            if not products:
                continue
            reminder_key = f"notify:stock:{club.id}:{bucket}:{reporting_periods()['local_now'].date().isoformat()}"
            if not await _notification_once(reminder_key, ttl=14 * 86400):
                continue
            text = _format_stock_reminder(club.name or "Клуб", [(str(name), int(stock)) for name, stock in products], bucket=bucket)
            try:
                await notify_stock_reminders(bot, club, session, text)
            except Exception as exc:
                await _notification_forget(reminder_key)
                logger.error("Не удалось отправить напоминание по складу club=%s: %s", club.id, exc)


async def send_work_schedule_notice(mode: str):
    now = reporting_periods()["now"]
    async with AsyncSessionLocal() as session:
        clubs = (await session.execute(select(Club).where(Club.subscription_expire_at >= now))).scalars().all()
        for club in clubs:
            settings = club.club_settings if isinstance(club.club_settings, dict) else {}
            work_schedule = settings.get("work_schedule", {})
            if not settings.get("features", {}).get("work_schedule_reminders", True):
                continue
            if not work_schedule or not club.bot_token:
                continue
            bot = bots_dict.get(club.bot_token)
            if not bot:
                continue
            recipients = set()
            parents_res = await session.execute(select(Student.parent_id).where(Student.club_id == club.id, Student.parent_id.isnot(None)))
            recipients.update(int(pid) for pid in parents_res.scalars().all() if pid)
            linked_res = await session.execute(
                select(StudentParent.parent_id).join(Student, StudentParent.student_id == Student.id).where(Student.club_id == club.id)
            )
            recipients.update(int(pid) for pid in linked_res.scalars().all() if pid)
            if club.owner_id:
                recipients.add(int(club.owner_id))
            if not recipients:
                continue

            if mode == "sat":
                intro = "Наш клуб работает в субботу по следующему графику:"
                days = ["sat"]
            elif mode == "sun":
                intro = "Наш клуб работает в воскресенье по следующему графику:"
                days = ["sun"]
            else:
                intro = "Наш клуб работает по будням по следующему графику:"
                days = ["mon", "tue", "wed", "thu", "fri"]

            text = _format_work_schedule_notice(
                club.name or "Клуб",
                work_schedule,
                days,
                f"{intro}\n\nГрафик занятий можете посмотреть во вкладке «Расписание».",
            )
            for chat_id in recipients:
                try:
                    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                    await asyncio.sleep(0.03)
                except Exception as exc:
                    logger.warning("Не удалось отправить график работы club=%s chat=%s: %s", club.id, chat_id, exc)


async def check_abon_mailing():
    """Рассылка уведомлений об истекающих абонементах."""
    async with AsyncSessionLocal() as session:
        data = await get_expire_students_grouped(session)

        logger.info("🚀 SaaS Рассылка: Найдено %s атлетов с истекающими абонементами.", len(data))

        for student, token in data:
            try:
                current_bot = bots_dict.get(token)
                if not current_bot:
                    logger.warning("⚠️ Бот с токеном ...%s не найден в bots_dict", token[-8:])
                    continue

                club_res = await session.execute(select(Club).where(Club.bot_token == token))
                club = club_res.scalar_one_or_none()
                club_settings = club.club_settings if club else {}

                parent_ids = await get_student_parent_ids(student.id, session)
                if not parent_ids:
                    logger.error(f"❌ Родители для Student ID {student.id} не найдены в базе данных.")
                    continue

                reminder_flags = _subscription_reminder_flags(student, reporting_periods()["now"])
                if not reminder_flags:
                    continue

                now = reporting_periods()["now"]
                today = reporting_periods()["local_now"].date()
                expire_str = student.expire_date.strftime("%d.%m.%Y") if student.expire_date else None
                reminder_codes = "-".join(f"{kind}{value}" for kind, value in reminder_flags)
                notification_key = f"notify:expire:{student.club_id}:{student.id}:{today.isoformat()}:{reminder_codes}"
                lines = [
                    "⚠️ <b>Внимание!</b>",
                    "",
                    f"У атлета <b>{escape(student.name)}</b> скоро закончится абонемент.",
                ]
                if expire_str:
                    lines.append(f"Дата окончания: <code>{expire_str}</code>")
                days_left = [value for kind, value in reminder_flags if kind == "days"]
                if days_left:
                    lines.append(f"Осталось по дате: <b>{min(days_left)} дн.</b>")
                lessons_left = [value for kind, value in reminder_flags if kind == "lessons"]
                if lessons_left:
                    lines.append(f"Осталось занятий: <b>{min(lessons_left)}</b>")
                lines.extend(["", "Не забудьте продлить его в меню! 🥊"])
                text = "\n".join(lines)

                if not await _notification_once(notification_key, ttl=7 * 86400):
                    continue
                for parent_id in parent_ids:
                    parent_user = await session.scalar(select(User).where(User.user_id == parent_id))
                    if not parent_user:
                        logger.warning(f"⚠️ Родитель {parent_id} для Student ID {student.id} отсутствует в users")
                        continue
                    reply_markup = get_profile_keyboard(
                        user=parent_user,
                        club_settings=club_settings,
                        is_authorized=True,
                        profile_mode="staff" if await is_staff_or_owner(session, club, parent_user.user_id) else "client",
                    )
                    await current_bot.send_message(chat_id=parent_id, text=text, parse_mode="HTML", reply_markup=reply_markup)
                    logger.info(f"✅ [Клуб {student.club_id}] Отправлено родителю {parent_id}")
                    await asyncio.sleep(0.05)

            except Exception as exc:
                if "notification_key" in locals():
                    await _notification_forget(notification_key)
                logger.error(f"❌ Ошибка отправки (Student ID {student.id}): {exc}")


async def send_daily_report_to_admins():
    """Рассылка вечерних бизнес-отчетов владельцам клубов."""
    periods = reporting_periods()
    now = periods["now"]
    start_of_today = periods["today"]
    start_of_yesterday = periods["yesterday"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Club).where(Club.subscription_expire_at >= now))
        clubs = result.scalars().all()

    for club in clubs:
        try:
            bot = bots_dict.get(club.bot_token)
            if not bot or not club.owner_id:
                continue

            async with AsyncSessionLocal() as session:
                visits, active_passes = await get_daily_stats(club_id=club.id, session=session)
                student_res = await session.execute(select(Student).where(Student.club_id == club.id))
                students = list(student_res.scalars().all())
                visit_log_res = await session.execute(
                    select(VisitLog).where(
                        VisitLog.club_id == club.id,
                        VisitLog.visited_at >= start_of_today,
                    )
                )
                visit_logs = list(visit_log_res.scalars().all())
                today_pay_res = await session.execute(
                    select(PaymentOrder).where(
                        PaymentOrder.club_id == club.id,
                        PaymentOrder.status == "CONFIRMED",
                        PaymentOrder.created_at >= start_of_today,
                    )
                )
                today_cart_res = await session.execute(
                    select(CartOrder).where(
                        CartOrder.club_id == club.id,
                        CartOrder.status == "CONFIRMED",
                        CartOrder.created_at >= start_of_today,
                    )
                )
                today_payments = list(today_pay_res.scalars().all()) + list(today_cart_res.scalars().all())
                yesterday_pay_res = await session.execute(
                    select(PaymentOrder).where(
                        PaymentOrder.club_id == club.id,
                        PaymentOrder.status == "CONFIRMED",
                        PaymentOrder.created_at >= start_of_yesterday,
                        PaymentOrder.created_at < start_of_today,
                    )
                )
                yesterday_cart_res = await session.execute(
                    select(CartOrder).where(
                        CartOrder.club_id == club.id,
                        CartOrder.status == "CONFIRMED",
                        CartOrder.created_at >= start_of_yesterday,
                        CartOrder.created_at < start_of_today,
                    )
                )
                yesterday_payments = list(yesterday_pay_res.scalars().all()) + list(yesterday_cart_res.scalars().all())

            biz_metrics = calculate_daily_business_report(students, today_payments, yesterday_payments, visit_logs=visit_logs)
            admin_metrics = calculate_admin_dashboard(students)

            expired_count = len(admin_metrics.get("expired_students", [])) if not admin_metrics.get("empty") else 0
            sleeping_count = len(admin_metrics.get("sleeping_students", [])) if not admin_metrics.get("empty") else 0
            config_disciplines = (club.club_settings or {}).get("disciplines", {})
            top_disc_key = biz_metrics["top_discipline"].lower()
            human_discipline_name = config_disciplines.get(top_disc_key, {}).get("name", biz_metrics["top_discipline"])

            report_text = (
                f"📊 <b>ГЛУБОКИЙ БИЗНЕС-ОТЧЕТ: {club.name}</b>\n"
                f"📅 Дата: <code>{now.strftime('%d.%m.%Y')}</code>\n\n"
                f"💰 <b>Касса сегодня:</b> <code>{biz_metrics['revenue_today']} ₽</code>\n"
                f"⚖️ <b>Динамика ко вчера:</b> <code>{biz_metrics['revenue_diff_text']}</code>\n"
                f"🥋 <b>Всего атлетов в базе:</b> <code>{biz_metrics['total_athletes']}</code>\n"
                f"👥 <b>Родителей с привязкой:</b> <code>{biz_metrics['total_parents']}</code>\n\n"
                f"📈 <b>ОПЕРАТИВНЫЙ АНАЛИЗ ЗА ДЕНЬ:</b>\n"
                f"🚶‍♂️ Посещений зала: <code>{visits}</code>\n"
                f"⚡️ Пиковые часы сегодня: <code>{biz_metrics['peak_hours']}</code>\n"
                f"🥋 Главное направление: <code>{human_discipline_name}</code>\n"
                f"💎 Действующих абонементов: <code>{active_passes}</code>\n\n"
                f"🚨 <b>МЕНЕДЖМЕНТ (Проверить админа):</b>\n"
                f"❌ Закончился баланс: <code>{expired_count} чел.</code> (ждут звонка)\n"
                f"last_visit 💤 Спящие (>14 дней): <code>{sleeping_count} чел.</code>\n"
            )

            await bot.send_message(club.owner_id, report_text, parse_mode="HTML")
            logger.info("🔥 Комплексный ИИ-отчет для клуба %s успешно отправлен боссу!", club.id)
            await asyncio.sleep(0.05)

        except Exception as exc:
            logger.error("❌ Ошибка отправки вечернего отчета клубу %s: %s", club.id, exc)


async def saas_daily_morning_check():
    """Ежедневная фоновая проверка: Дни рождения и Прогульщики (10 дней без посещений)."""
    logger.info("📊 Запуск фонового анализа базы атлетов...")
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Student))
        students = result.scalars().all()

        periods = reporting_periods()
        now_datetime = periods["now"]
        today = periods["local_now"].date()

        missing_birthdays = {}
        missing_subscriptions = {}
        for student in students:
            parent_ids = await get_student_parent_ids(student.id, session)
            if not parent_ids:
                continue
            if not student.birthday:
                for parent_id in parent_ids:
                    key = (student.club_id, parent_id)
                    missing_birthdays.setdefault(key, []).append(student.name)
            has_subscription = bool(
                student.expire_date
                and student.expire_date > now_datetime
                and (student.balance_lessons or 0) > 0
            )
            if not has_subscription:
                for parent_id in parent_ids:
                    key = (student.club_id, parent_id)
                    missing_subscriptions.setdefault(key, []).append(student.name)

        missing_club_ids = {club_id for club_id, _ in missing_birthdays} | {club_id for club_id, _ in missing_subscriptions}
        clubs_result = await session.execute(select(Club).where(Club.id.in_(missing_club_ids))) if missing_club_ids else None
        clubs_by_id = {club.id: club for club in clubs_result.scalars().all()} if clubs_result else {}

        for (club_id, parent_id), names in missing_birthdays.items():
            club = clubs_by_id.get(club_id)
            if not club or not club.subscription_expire_at or club.subscription_expire_at < now_datetime or club.bot_token not in bots_dict:
                continue
            settings = club.club_settings if isinstance(club.club_settings, dict) else {}
            notification_key = f"notify:birthday-missing:{club_id}:{parent_id}:{today.isoformat()}"
            try:
                if not settings.get("features", {}).get("birthday_missing_reminders", True):
                    continue
                if not await _notification_once(notification_key):
                    continue
                await bots_dict[club.bot_token].send_message(
                    chat_id=parent_id,
                    text=(
                        f"🎂 <b>Заполните даты рождения атлетов</b>\n\n"
                        f"В профиле клуба <b>{escape(club.name)}</b> не указана дата рождения: "
                        f"<b>{escape(', '.join(names))}</b>.\n"
                        "Это нужно для корректного возраста, тарифов и статистики."
                    ),
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                        types.InlineKeyboardButton(text="Указать дату рождения", callback_data="edit_birthday")
                    ]]),
                    parse_mode="HTML",
                )
            except Exception as reminder_error:
                await _notification_forget(notification_key)
                logger.error("Не удалось отправить напоминание о ДР родителю %s: %s", parent_id, reminder_error)

        for (club_id, parent_id), names in missing_subscriptions.items():
            club = clubs_by_id.get(club_id)
            if not club or not club.subscription_expire_at or club.subscription_expire_at < now_datetime or club.bot_token not in bots_dict:
                continue
            settings = club.club_settings if isinstance(club.club_settings, dict) else {}
            notification_key = f"notify:no-subscription:{club_id}:{parent_id}:{today.isoformat()}"
            try:
                if not settings.get("features", {}).get("subscription_expiry_reminders", True):
                    continue
                if not await _notification_once(notification_key):
                    continue
                await bots_dict[club.bot_token].send_message(
                    chat_id=parent_id,
                    text=(
                        f"💳 <b>Нет активного абонемента</b>\n\n"
                        f"У атлета(ов) <b>{escape(', '.join(names))}</b> в клубе "
                        f"<b>{escape(club.name)}</b> нет действующего абонемента.\n"
                        "Выберите тариф в меню, чтобы продолжить тренировки."
                    ),
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                        types.InlineKeyboardButton(text="💳 Выбрать абонемент", callback_data="choose_section")
                    ]]),
                    parse_mode="HTML",
                )
            except Exception as subscription_error:
                await _notification_forget(notification_key)
                logger.error("Не удалось отправить напоминание об абонементе родителю %s: %s", parent_id, subscription_error)

        for student in students:
            club_result = await session.execute(select(Club).where(Club.id == student.club_id))
            club = club_result.scalar_one_or_none()
            if not club or not club.subscription_expire_at or club.subscription_expire_at < now_datetime or club.bot_token not in bots_dict:
                continue
            bot = bots_dict[club.bot_token]
            settings = club.club_settings if isinstance(club.club_settings, dict) else {}

            parent_ids = await get_student_parent_ids(student.id, session)
            if not parent_ids:
                continue

            if hasattr(student, "birthday") and student.birthday:
                if student.birthday.month == today.month and student.birthday.day == today.day:
                    try:
                        birthday_key = f"notify:birthday:{student.club_id}:{student.id}:{today.year}"
                        if settings.get("features", {}).get("birthday_greetings", True) and await _notification_once(birthday_key, ttl=370 * 86400):
                            for parent_id in parent_ids:
                                await bot.send_message(
                                chat_id=parent_id,
                                text=(
                                    f"🎂 Клуб <b>{escape(club.name)}</b> поздравляет атлета <b>{escape(student.name)}</b> "
                                    f"с Днём Рождения! 🎉\n"
                                    f"Желаем новых спортивных побед и крепкого здоровья!"
                                ),
                                parse_mode="HTML",
                            )
                                logger.info(f"🎉 Поздравление с ДР отправлено атлету {student.name} (Родитель: {parent_id})")
                    except Exception as e:
                        await _notification_forget(birthday_key)
                        logger.error(f"Не удалось отправить ДР сообщение атлету {student.id}: {e}")

            if student.last_visit and not student.is_frozen:
                last_visit = student.last_visit.replace(tzinfo=None)
                days_absent = max(0, (now_datetime - last_visit).days)
                absence_threshold = max((value for value in (5, 10, 15, 20) if days_absent >= value), default=0)
                if absence_threshold:
                    try:
                        notice_key = f"notify:absent:{student.club_id}:{student.id}:{last_visit.date().isoformat()}:{absence_threshold}"
                        if not settings.get("features", {}).get("absence_reminders", True):
                            continue
                        if not await _notification_once(notice_key, ttl=45 * 86400):
                            continue
                        for parent_id in parent_ids:
                            await bot.send_message(
                            chat_id=parent_id,
                            text=(
                                f"👋 Здравствуйте! Мы заметили, что атлет <b>{escape(student.name)}</b> "
                                f"не посещал тренировки уже {absence_threshold} дней. Мы соскучились! "
                                "Ждём вас на занятиях. 😉"
                            ),
                            parse_mode="HTML",
                        )
                            logger.info(f"📢 Уведомление о прогуле (10 дней) отправлено атлету {student.name} родителю {parent_id}")
                    except Exception as e:
                        await _notification_forget(notice_key)
                        logger.error("Не удалось отправить уведомление прогульщику: %s", e)






