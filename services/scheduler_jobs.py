from __future__ import annotations

import asyncio
import os
from datetime import datetime
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
    User,
    VisitLog,
    create_db_backup,
    get_daily_stats,
    get_expire_students_grouped,
)
from handlers.buttons import get_profile_keyboard
from services.order_notifications import notify_stock_reminders
from services.analytics import calculate_admin_dashboard, calculate_daily_business_report, reporting_periods
from services.bot_registry import bots_dict


async def send_backup_to_admin():
    """РЎРѕР·РґР°РµС‚ Р±СЌРєР°Рї РІСЃРµР№ Р‘Р” Рё РѕС‚РїСЂР°РІР»СЏРµС‚ РЎСѓРїРµСЂ-Р°РґРјРёРЅР°Рј."""
    path = await create_db_backup()
    if not path or not os.path.exists(path):
        logger.error("вќЊ Р¤Р°Р№Р» Р±СЌРєР°РїР° РЅРµ Р±С‹Р» СЃРѕР·РґР°РЅ!")
        return

    random_bot = next(iter(bots_dict.values()), None)
    if not random_bot:
        logger.error("вќЊ РќРµС‚ Р°РєС‚РёРІРЅС‹С… Р±РѕС‚РѕРІ РґР»СЏ РѕС‚РїСЂР°РІРєРё Р±СЌРєР°РїР°!")
        return

    for admin_id in ADMIN_IDS:
        try:
            await random_bot.send_document(
                chat_id=admin_id,
                document=types.FSInputFile(path),
                caption=f"рџ“¦ <b>SaaS Full Backup</b>\nрџ“… Р”Р°С‚Р°: <code>{datetime.now().strftime('%d.%m.%Y')}</code>",
            )
            logger.info("вњ… Р‘СЌРєР°Рї РѕС‚РїСЂР°РІР»РµРЅ СЃСѓРїРµСЂ-Р°РґРјРёРЅСѓ %s", admin_id)
        except Exception as exc:
            logger.error("вќЊ РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё Р±СЌРєР°РїР° Р°РґРјРёРЅСѓ %s: %s", admin_id, exc)

    if os.path.exists(path):
        os.remove(path)
        logger.debug("рџ—‘пёЏ Р’СЂРµРјРµРЅРЅС‹Р№ С„Р°Р№Р» Р±СЌРєР°РїР° СѓРґР°Р»РµРЅ СЃ РґРёСЃРєР°")


async def _notification_once(key: str, ttl: int = 86400) -> bool:
    """Protect scheduled Telegram notifications from duplicate sends."""
    try:
        from main import redis_client

        return bool(await redis_client.set(key, "1", ex=ttl, nx=True))
    except Exception as error:
        logger.warning("РќРµ СѓРґР°Р»РѕСЃСЊ РїСЂРѕРІРµСЂРёС‚СЊ idempotency СѓРІРµРґРѕРјР»РµРЅРёСЏ %s: %s", key, error)
        return True


async def _notification_forget(key: str) -> None:
    """Release an idempotency lock when delivery failed."""
    try:
        from main import redis_client

        await redis_client.delete(key)
    except Exception as error:
        logger.warning("РќРµ СѓРґР°Р»РѕСЃСЊ СЃРЅСЏС‚СЊ Р±Р»РѕРєРёСЂРѕРІРєСѓ СѓРІРµРґРѕРјР»РµРЅРёСЏ %s: %s", key, error)


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
    day_names = {"mon": "РџРЅ", "tue": "Р’С‚", "wed": "РЎСЂ", "thu": "Р§С‚", "fri": "РџС‚", "sat": "РЎР±", "sun": "Р’СЃ"}
    lines = [f"рџ•’ <b>Р“СЂР°С„РёРє СЂР°Р±РѕС‚С‹</b>", f"рџЏџ <b>{escape(club_name)}</b>", ""]
    for key in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
        row = work_schedule.get(key) or {}
        if row:
            open_at = escape(str(row.get("open", "вЂ”")))
            close_at = escape(str(row.get("close", "вЂ”")))
            note = escape(str(row.get("note", "")).strip())
            suffix = f" В· {note}" if note else ""
            lines.append(f"{day_names[key]}: <b>{open_at}вЂ“{close_at}</b>{suffix}")
        else:
            lines.append(f"{day_names[key]}: <b>РЅРµ Р·Р°РґР°РЅ</b>")
    lines.extend(["", "РџСЂРѕРІРµСЂСЊС‚Рµ РіСЂР°С„РёРє РїРµСЂРµРґ РІРёР·РёС‚РѕРј."])
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
            if club.owner_id:
                recipients.add(int(club.owner_id))
            if not recipients:
                continue
            text = _format_work_schedule(club.name or "РљР»СѓР±", work_schedule)
            for chat_id in recipients:
                try:
                    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                    await asyncio.sleep(0.03)
                except Exception as exc:
                    logger.warning("РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ РіСЂР°С„РёРє СЂР°Р±РѕС‚С‹ club=%s chat=%s: %s", club.id, chat_id, exc)


def _format_work_schedule_notice(club_name: str, work_schedule: dict, days: list[str], intro: str) -> str:
    day_names = {"mon": "РџРЅ", "tue": "Р’С‚", "wed": "РЎСЂ", "thu": "Р§С‚", "fri": "РџС‚", "sat": "РЎР±", "sun": "Р’СЃ"}
    lines = [intro, f"рџЏџ <b>{escape(club_name)}</b>", ""]
    for key in days:
        row = work_schedule.get(key) or {}
        if row:
            open_at = escape(str(row.get("open", "вЂ”")))
            close_at = escape(str(row.get("close", "вЂ”")))
            note = escape(str(row.get("note", "")).strip())
            suffix = f" В· {note}" if note else ""
            lines.append(f"{day_names[key]}: <b>{open_at}вЂ“{close_at}</b>{suffix}")
        else:
            lines.append(f"{day_names[key]}: <b>РЅРµ Р·Р°РґР°РЅ</b>")
    lines.extend(["", "РџСЂРѕРІРµСЂСЊС‚Рµ РіСЂР°С„РёРє РїРµСЂРµРґ РІРёР·РёС‚РѕРј."])
    return "\n".join(lines)


def _format_stock_reminder(club_name: str, products: list[tuple[str, int]], *, bucket: str) -> str:
    intro = "РЈС‚СЂРµРЅРЅРµРµ" if bucket == "am" else "Р’РµС‡РµСЂРЅРµРµ"
    lines = [
        f"рџ“¦ <b>{intro} РЅР°РїРѕРјРёРЅР°РЅРёРµ РїРѕ СЃРєР»Р°РґСѓ</b>",
        f"рџЏџ <b>{escape(club_name)}</b>",
        "",
        "Р—Р°РєСѓРїРёС‚Рµ С‚РѕРІР°СЂС‹, Сѓ РєРѕС‚РѕСЂС‹С… РѕСЃС‚Р°С‚РѕРє РЅР° СѓСЂРѕРІРЅРµ 3 Рё РЅРёР¶Рµ:",
    ]
    for name, stock in products:
        lines.append(f"вЂў {escape(name)} вЂ” <b>{stock}</b> С€С‚.")
    lines.extend(["", "РџСЂРѕРІРµСЂСЊС‚Рµ СЃРєР»Р°Рґ Рё РїРѕРїРѕР»РЅРёС‚Рµ РѕСЃС‚Р°С‚РѕРє."])
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
            text = _format_stock_reminder(club.name or "РљР»СѓР±", [(str(name), int(stock)) for name, stock in products], bucket=bucket)
            try:
                await notify_stock_reminders(bot, club, session, text)
            except Exception as exc:
                await _notification_forget(reminder_key)
                logger.error("РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ РЅР°РїРѕРјРёРЅР°РЅРёРµ РїРѕ СЃРєР»Р°РґСѓ club=%s: %s", club.id, exc)


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
            if club.owner_id:
                recipients.add(int(club.owner_id))
            if not recipients:
                continue

            if mode == "sat":
                intro = "РќР°С€ РєР»СѓР± СЂР°Р±РѕС‚Р°РµС‚ РІ СЃСѓР±Р±РѕС‚Сѓ РїРѕ СЃР»РµРґСѓСЋС‰РµРјСѓ РіСЂР°С„РёРєСѓ:"
                days = ["sat"]
            elif mode == "sun":
                intro = "РќР°С€ РєР»СѓР± СЂР°Р±РѕС‚Р°РµС‚ РІ РІРѕСЃРєСЂРµСЃРµРЅСЊРµ РїРѕ СЃР»РµРґСѓСЋС‰РµРјСѓ РіСЂР°С„РёРєСѓ:"
                days = ["sun"]
            else:
                intro = "РќР°С€ РєР»СѓР± СЂР°Р±РѕС‚Р°РµС‚ РІ РїРѕРЅРµРґРµР»СЊРЅРёРє РїРѕ СЃР»РµРґСѓСЋС‰РµРјСѓ РіСЂР°С„РёРєСѓ:"
                days = ["mon", "tue", "wed", "thu", "fri"]

            text = _format_work_schedule_notice(
                club.name or "РљР»СѓР±",
                work_schedule,
                days,
                f"{intro}\n\nР“СЂР°С„РёРє Р·Р°РЅСЏС‚РёР№ РјРѕР¶РµС‚Рµ РїРѕСЃРјРѕС‚СЂРµС‚СЊ РІРѕ РІРєР»Р°РґРєРµ В«Р Р°СЃРїРёСЃР°РЅРёРµВ».",
            )
            for chat_id in recipients:
                try:
                    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                    await asyncio.sleep(0.03)
                except Exception as exc:
                    logger.warning("РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ РіСЂР°С„РёРє СЂР°Р±РѕС‚С‹ club=%s chat=%s: %s", club.id, chat_id, exc)


async def check_abon_mailing():
    """Р Р°СЃСЃС‹Р»РєР° СѓРІРµРґРѕРјР»РµРЅРёР№ РѕР± РёСЃС‚РµРєР°СЋС‰РёС… Р°Р±РѕРЅРµРјРµРЅС‚Р°С…."""
    async with AsyncSessionLocal() as session:
        data = await get_expire_students_grouped(session)

        logger.info("рџљЂ SaaS Р Р°СЃСЃС‹Р»РєР°: РќР°Р№РґРµРЅРѕ %s Р°С‚Р»РµС‚РѕРІ СЃ РёСЃС‚РµРєР°СЋС‰РёРјРё Р°Р±РѕРЅРµРјРµРЅС‚Р°РјРё.", len(data))

        for student, token in data:
            try:
                current_bot = bots_dict.get(token)
                if not current_bot:
                    logger.warning("вљ пёЏ Р‘РѕС‚ СЃ С‚РѕРєРµРЅРѕРј ...%s РЅРµ РЅР°Р№РґРµРЅ РІ bots_dict", token[-8:])
                    continue

                club_res = await session.execute(select(Club).where(Club.bot_token == token))
                club = club_res.scalar_one_or_none()
                club_settings = club.club_settings if club else {}

                user_res = await session.execute(select(User).where(User.user_id == student.parent_id))
                parent_user = user_res.scalar_one_or_none()
                if not parent_user:
                    logger.error("вќЊ Р РѕРґС‚РµР»СЊ СЃ ID %s РЅРµ РЅР°Р№РґРµРЅ РІ Р±Р°Р·Рµ РґР°РЅРЅС‹С….", student.parent_id)
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
                    "вљ пёЏ <b>Р’РЅРёРјР°РЅРёРµ!</b>",
                    "",
                    f"РЈ Р°С‚Р»РµС‚Р° <b>{escape(student.name)}</b> СЃРєРѕСЂРѕ Р·Р°РєРѕРЅС‡РёС‚СЃСЏ Р°Р±РѕРЅРµРјРµРЅС‚.",
                ]
                if expire_str:
                    lines.append(f"Р”Р°С‚Р° РѕРєРѕРЅС‡Р°РЅРёСЏ: <code>{expire_str}</code>")
                days_left = [value for kind, value in reminder_flags if kind == "days"]
                if days_left:
                    lines.append(f"РћСЃС‚Р°Р»РѕСЃСЊ РїРѕ РґР°С‚Рµ: <b>{min(days_left)} РґРЅ.</b>")
                lessons_left = [value for kind, value in reminder_flags if kind == "lessons"]
                if lessons_left:
                    lines.append(f"РћСЃС‚Р°Р»РѕСЃСЊ Р·Р°РЅСЏС‚РёР№: <b>{min(lessons_left)}</b>")
                lines.extend(["", "РќРµ Р·Р°Р±СѓРґСЊС‚Рµ РїСЂРѕРґР»РёС‚СЊ РµРіРѕ РІ РјРµРЅСЋ! рџҐЉ"])
                text = "\n".join(lines)

                reply_markup = get_profile_keyboard(
                    user=parent_user,
                    club_settings=club_settings,
                    is_authorized=True,
                )

                if not await _notification_once(notification_key, ttl=7 * 86400):
                    continue
                await current_bot.send_message(
                    chat_id=student.parent_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )

                logger.info("вњ… [РљР»СѓР± %s] РћС‚РїСЂР°РІР»РµРЅРѕ СЂРѕРґРёС‚РµР»СЋ %s", student.club_id, student.parent_id)
                await asyncio.sleep(0.05)

            except Exception as exc:
                if "notification_key" in locals():
                    await _notification_forget(notification_key)
                logger.error("вќЊ РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё (Student ID %s): %s", student.id, exc)


async def send_daily_report_to_admins():
    """Р Р°СЃСЃС‹Р»РєР° РІРµС‡РµСЂРЅРёС… Р±РёР·РЅРµСЃ-РѕС‚С‡РµС‚РѕРІ РІР»Р°РґРµР»СЊС†Р°Рј РєР»СѓР±РѕРІ."""
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
                f"рџ“Љ <b>Р“Р›РЈР‘РћРљРР™ Р‘РР—РќР•РЎ-РћРўР§Р•Рў: {club.name}</b>\n"
                f"рџ“… Р”Р°С‚Р°: <code>{now.strftime('%d.%m.%Y')}</code>\n\n"
                f"рџ’° <b>РљР°СЃСЃР° СЃРµРіРѕРґРЅСЏ:</b> <code>{biz_metrics['revenue_today']} в‚Ѕ</code>\n"
                f"вљ–пёЏ <b>Р”РёРЅР°РјРёРєР° РєРѕ РІС‡РµСЂР°:</b> <code>{biz_metrics['revenue_diff_text']}</code>\n"
                f"рџҐ‹ <b>Р’СЃРµРіРѕ Р°С‚Р»РµС‚РѕРІ РІ Р±Р°Р·Рµ:</b> <code>{biz_metrics['total_athletes']}</code>\n"
                f"рџ‘Ґ <b>Р РѕРґРёС‚РµР»РµР№ СЃ РїСЂРёРІСЏР·РєРѕР№:</b> <code>{biz_metrics['total_parents']}</code>\n\n"
                f"рџ“€ <b>РћРџР•Р РђРўРР’РќР«Р™ РђРќРђР›РР— Р—Рђ Р”Р•РќР¬:</b>\n"
                f"рџљ¶вЂЌв™‚пёЏ РџРѕСЃРµС‰РµРЅРёР№ Р·Р°Р»Р°: <code>{visits}</code>\n"
                f"вљЎпёЏ РџРёРєРѕРІС‹Рµ С‡Р°СЃС‹ СЃРµРіРѕРґРЅСЏ: <code>{biz_metrics['peak_hours']}</code>\n"
                f"рџҐ‹ Р“Р»Р°РІРЅРѕРµ РЅР°РїСЂР°РІР»РµРЅРёРµ: <code>{human_discipline_name}</code>\n"
                f"рџ’Ћ Р”РµР№СЃС‚РІСѓСЋС‰РёС… Р°Р±РѕРЅРµРјРµРЅС‚РѕРІ: <code>{active_passes}</code>\n\n"
                f"рџљЁ <b>РњР•РќР•Р”Р–РњР•РќРў (РџСЂРѕРІРµСЂРёС‚СЊ Р°РґРјРёРЅР°):</b>\n"
                f"вќЊ Р—Р°РєРѕРЅС‡РёР»СЃСЏ Р±Р°Р»Р°РЅСЃ: <code>{expired_count} С‡РµР».</code> (Р¶РґСѓС‚ Р·РІРѕРЅРєР°)\n"
                f"last_visit рџ’¤ РЎРїСЏС‰РёРµ (>14 РґРЅРµР№): <code>{sleeping_count} С‡РµР».</code>\n"
            )

            await bot.send_message(club.owner_id, report_text, parse_mode="HTML")
            logger.info("рџ”Ґ РљРѕРјРїР»РµРєСЃРЅС‹Р№ РР-РѕС‚С‡РµС‚ РґР»СЏ РєР»СѓР±Р° %s СѓСЃРїРµС€РЅРѕ РѕС‚РїСЂР°РІР»РµРЅ Р±РѕСЃСЃСѓ!", club.id)
            await asyncio.sleep(0.05)

        except Exception as exc:
            logger.error("вќЊ РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё РІРµС‡РµСЂРЅРµРіРѕ РѕС‚С‡РµС‚Р° РєР»СѓР±Сѓ %s: %s", club.id, exc)


async def saas_daily_morning_check():
    """Р•Р¶РµРґРЅРµРІРЅР°СЏ С„РѕРЅРѕРІР°СЏ РїСЂРѕРІРµСЂРєР°: Р”РЅРё СЂРѕР¶РґРµРЅРёСЏ Рё РџСЂРѕРіСѓР»СЊС‰РёРєРё (10 РґРЅРµР№ Р±РµР· РїРѕСЃРµС‰РµРЅРёР№)."""
    logger.info("рџ“Љ Р—Р°РїСѓСЃРє С„РѕРЅРѕРІРѕРіРѕ Р°РЅР°Р»РёР·Р° Р±Р°Р·С‹ Р°С‚Р»РµС‚РѕРІ...")
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Student))
        students = result.scalars().all()

        periods = reporting_periods()
        now_datetime = periods["now"]
        today = periods["local_now"].date()

        missing_birthdays = {}
        missing_subscriptions = {}
        for student in students:
            if student.parent_id and not student.birthday:
                key = (student.club_id, student.parent_id)
                missing_birthdays.setdefault(key, []).append(student.name)
            has_subscription = bool(
                student.expire_date
                and student.expire_date > now_datetime
                and (student.balance_lessons or 0) > 0
            )
            if student.parent_id and not has_subscription:
                key = (student.club_id, student.parent_id)
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
                        f"рџЋ‚ <b>Р—Р°РїРѕР»РЅРёС‚Рµ РґР°С‚С‹ СЂРѕР¶РґРµРЅРёСЏ Р°С‚Р»РµС‚РѕРІ</b>\n\n"
                        f"Р’ РїСЂРѕС„РёР»Рµ РєР»СѓР±Р° <b>{escape(club.name)}</b> РЅРµ СѓРєР°Р·Р°РЅР° РґР°С‚Р° СЂРѕР¶РґРµРЅРёСЏ: "
                        f"<b>{escape(', '.join(names))}</b>.\n"
                        "Р­С‚Рѕ РЅСѓР¶РЅРѕ РґР»СЏ РєРѕСЂСЂРµРєС‚РЅРѕРіРѕ РІРѕР·СЂР°СЃС‚Р°, С‚Р°СЂРёС„РѕРІ Рё СЃС‚Р°С‚РёСЃС‚РёРєРё."
                    ),
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                        types.InlineKeyboardButton(text="РЈРєР°Р·Р°С‚СЊ РґР°С‚Сѓ СЂРѕР¶РґРµРЅРёСЏ", callback_data="edit_birthday")
                    ]]),
                    parse_mode="HTML",
                )
            except Exception as reminder_error:
                await _notification_forget(notification_key)
                logger.error("РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ РЅР°РїРѕРјРёРЅР°РЅРёРµ Рѕ Р”Р  СЂРѕРґРёС‚РµР»СЋ %s: %s", parent_id, reminder_error)

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
                        f"рџ’і <b>РќРµС‚ Р°РєС‚РёРІРЅРѕРіРѕ Р°Р±РѕРЅРµРјРµРЅС‚Р°</b>\n\n"
                        f"РЈ Р°С‚Р»РµС‚Р°(РѕРІ) <b>{escape(', '.join(names))}</b> РІ РєР»СѓР±Рµ "
                        f"<b>{escape(club.name)}</b> РЅРµС‚ РґРµР№СЃС‚РІСѓСЋС‰РµРіРѕ Р°Р±РѕРЅРµРјРµРЅС‚Р°.\n"
                        "Р’С‹Р±РµСЂРёС‚Рµ С‚Р°СЂРёС„ РІ РјРµРЅСЋ, С‡С‚РѕР±С‹ РїСЂРѕРґРѕР»Р¶РёС‚СЊ С‚СЂРµРЅРёСЂРѕРІРєРё."
                    ),
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                        types.InlineKeyboardButton(text="рџ’і Р’С‹Р±СЂР°С‚СЊ Р°Р±РѕРЅРµРјРµРЅС‚", callback_data="choose_section")
                    ]]),
                    parse_mode="HTML",
                )
            except Exception as subscription_error:
                await _notification_forget(notification_key)
                logger.error("РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ РЅР°РїРѕРјРёРЅР°РЅРёРµ РѕР± Р°Р±РѕРЅРµРјРµРЅС‚Рµ СЂРѕРґРёС‚РµР»СЋ %s: %s", parent_id, subscription_error)

        for student in students:
            club_result = await session.execute(select(Club).where(Club.id == student.club_id))
            club = club_result.scalar_one_or_none()
            if not club or not club.subscription_expire_at or club.subscription_expire_at < now_datetime or club.bot_token not in bots_dict:
                continue
            bot = bots_dict[club.bot_token]
            settings = club.club_settings if isinstance(club.club_settings, dict) else {}

            if not student.parent_id:
                continue

            if hasattr(student, "birthday") and student.birthday:
                if student.birthday.month == today.month and student.birthday.day == today.day:
                    try:
                        birthday_key = f"notify:birthday:{student.club_id}:{student.id}:{today.year}"
                        if settings.get("features", {}).get("birthday_greetings", True) and await _notification_once(birthday_key, ttl=370 * 86400):
                            await bot.send_message(
                                chat_id=student.parent_id,
                                text=(
                                    f"рџЋ‚ РљР»СѓР± <b>{escape(club.name)}</b> РїРѕР·РґСЂР°РІР»СЏРµС‚ Р°С‚Р»РµС‚Р° <b>{escape(student.name)}</b> "
                                    f"СЃ Р”РЅС‘Рј Р РѕР¶РґРµРЅРёСЏ! рџЋ‰\n"
                                    f"Р–РµР»Р°РµРј РЅРѕРІС‹С… СЃРїРѕСЂС‚РёРІРЅС‹С… РїРѕР±РµРґ Рё РєСЂРµРїРєРѕРіРѕ Р·РґРѕСЂРѕРІСЊСЏ!"
                                ),
                                parse_mode="HTML",
                            )
                            logger.info("рџЋ‰ РџРѕР·РґСЂР°РІР»РµРЅРёРµ СЃ Р”Р  РѕС‚РїСЂР°РІР»РµРЅРѕ Р°С‚Р»РµС‚Сѓ %s (Р РѕРґРёС‚РµР»СЊ: %s)", student.name, student.parent_id)
                    except Exception as e:
                        await _notification_forget(birthday_key)
                        logger.error("РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ Р”Р  СЃРѕРѕР±С‰РµРЅРёРµ СЂРѕРґРёС‚РµР»СЋ %s: %s", student.parent_id, e)

            if student.last_visit and student.balance_lessons > 0 and not student.is_frozen:
                last_visit = student.last_visit.replace(tzinfo=None)
                days_absent = max(0, (now_datetime - last_visit).days)
                if days_absent >= 10:
                    try:
                        notice_key = f"notify:absent:{student.club_id}:{student.id}:{last_visit.date().isoformat()}:10"
                        if not settings.get("features", {}).get("absence_reminders", True):
                            continue
                        if not await _notification_once(notice_key, ttl=45 * 86400):
                            continue
                        await bot.send_message(
                            chat_id=student.parent_id,
                            text=(
                                f"рџ‘‹ Р—РґСЂР°РІСЃС‚РІСѓР№С‚Рµ! РњС‹ Р·Р°РјРµС‚РёР»Рё, С‡С‚Рѕ Р°С‚Р»РµС‚ <b>{escape(student.name)}</b> "
                                f"РЅРµ РїРѕСЃРµС‰Р°Р» С‚СЂРµРЅРёСЂРѕРІРєРё СѓР¶Рµ {days_absent} РґРЅРµР№. РњС‹ СЃРѕСЃРєСѓС‡РёР»РёСЃСЊ! "
                                "Р–РґС‘Рј РІР°СЃ РЅР° Р·Р°РЅСЏС‚РёСЏС…. рџ‰"
                            ),
                            parse_mode="HTML",
                        )
                        logger.info("рџ“ў РЈРІРµРґРѕРјР»РµРЅРёРµ Рѕ РїСЂРѕРіСѓР»Рµ (10 РґРЅРµР№) РѕС‚РїСЂР°РІР»РµРЅРѕ Р°С‚Р»РµС‚Сѓ %s", student.name)
                    except Exception as e:
                        await _notification_forget(notice_key)
                        logger.error("РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ СѓРІРµРґРѕРјР»РµРЅРёРµ РїСЂРѕРіСѓР»СЊС‰РёРєСѓ: %s", e)






