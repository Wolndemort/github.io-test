from __future__ import annotations

from datetime import datetime, timedelta
from html import escape

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.db import (
    AsyncSessionLocal, Club, PaymentOrder, Student, Survey, SurveyDelivery,
    SurveyQuestion, get_student_parent_ids,
)
from services.analytics import reporting_periods
from services.audit import audit_event
from services.bot_registry import bots_dict
from services.delivery import telegram_call, telegram_pace


CONFIRMED_STATUSES = ("CONFIRMED", "SUCCEEDED", "PAID")


def _last_activity(student: Student) -> datetime | None:
    value = student.last_visit or student.created_at
    return value.replace(tzinfo=None) if value else None


async def _purchased_tariff_keys(session, club: Club, student: Student) -> set[str]:
    order = (await session.execute(
        select(PaymentOrder)
        .where(
            PaymentOrder.club_id == club.id,
            PaymentOrder.student_id == student.id,
            PaymentOrder.status.in_(CONFIRMED_STATUSES),
            PaymentOrder.type.not_like("FREEZE%"),
        )
        .order_by(PaymentOrder.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if not order:
        return set()
    disciplines = (club.club_settings or {}).get("disciplines", {})
    keys: set[str] = set()
    for code, block in disciplines.items():
        if order.discipline and str(order.discipline) != str(code):
            continue
        for index, tariff in enumerate((block or {}).get("tariffs", []) or []):
            if int(tariff.get("days", 30) or 30) != int(order.days_to_add or 0):
                continue
            if int(tariff.get("count", 0) or 0) != int(order.lesson_count or 0):
                continue
            if tariff.get("active", True):
                keys.add(f"{code}:{index}")
    return keys


async def _matches_filters(session, club: Club, student: Student, survey: Survey, today: datetime) -> tuple[bool, str]:
    filters = survey.filters or {}
    activity = _last_activity(student)
    if not activity:
        return False, "no_activity"
    inactive_days = max(0, (today.date() - activity.date()).days)
    threshold = int(filters.get("absence_days") or survey.trigger_days or 0)
    if threshold and inactive_days < threshold:
        return False, "threshold"
    if filters.get("require_no_renewal", True) and student.expire_date:
        expire_date = student.expire_date.replace(tzinfo=None)
        if expire_date.date() > today.date():
            return False, "active_subscription"
    wanted_disciplines = {str(x) for x in (filters.get("disciplines") or []) if str(x)}
    if wanted_disciplines and str(student.discipline or "") not in wanted_disciplines:
        return False, "discipline"
    wanted_tariffs = {str(x) for x in (filters.get("tariffs") or []) if str(x)}
    if wanted_tariffs and not (wanted_tariffs & await _purchased_tariff_keys(session, club, student)):
        return False, "tariff"
    return True, f"absence:{inactive_days}:threshold:{threshold}:activity:{activity.isoformat()}"


def _question_keyboard(delivery_id: int, question: SurveyQuestion) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, option in enumerate(question.options or []):
        label = str(option)[:60]
        builder.button(text=label, callback_data=f"survey_answer:{delivery_id}:{question.id}:{index}")
    builder.adjust(1)
    return builder.as_markup()


async def send_scheduled_surveys() -> None:
    """Send each configured inactivity survey once per absence cycle and athlete."""
    now = reporting_periods()["local_now"].replace(tzinfo=None)
    async with AsyncSessionLocal() as session:
        surveys = (await session.scalars(
            select(Survey).where(Survey.is_active.is_(True), Survey.trigger_days.is_not(None), Survey.trigger_days > 0)
        )).all()
        for survey in surveys:
            club = await session.get(Club, survey.club_id)
            if not club or not club.bot_token:
                continue
            if not (club.club_settings or {}).get("features", {}).get("survey_scheduler", True):
                continue
            bot = bots_dict.get(club.bot_token)
            if not bot:
                continue
            students = (await session.scalars(select(Student).where(Student.club_id == club.id))).all()
            questions = (await session.scalars(
                select(SurveyQuestion).where(SurveyQuestion.survey_id == survey.id).order_by(SurveyQuestion.position)
            )).all()
            if not questions:
                continue
            for student in students:
                matches, cycle_key = await _matches_filters(session, club, student, survey, now)
                if not matches:
                    continue
                for recipient_id in await get_student_parent_ids(student.id, session):
                    async with session.begin_nested():
                        delivery = SurveyDelivery(
                            survey_id=survey.id, club_id=club.id, student_id=student.id,
                            recipient_id=int(recipient_id), trigger_key=cycle_key,
                        )
                        session.add(delivery)
                        try:
                            await session.flush()
                        except IntegrityError:
                            delivery = None
                    if not delivery:
                        continue
                    builder = InlineKeyboardBuilder()
                    builder.button(text="📝 Пройти опрос", callback_data=f"survey_start:{delivery.id}")
                    text = (
                        f"{survey.intro_text}\n\n"
                        f"Атлет: <b>{escape(student.name)}</b>\n"
                        "Ваши ответы помогут сделать тренировки удобнее."
                    )
                    try:
                        await telegram_pace(f"club:{club.id}")
                        await telegram_call(lambda: bot.send_message(int(recipient_id), text, parse_mode="HTML", reply_markup=builder.as_markup()))
                        delivery.status = "sent"
                        delivery.sent_at = datetime.utcnow()
                        await session.commit()
                        audit_event("survey_sent", club_id=club.id, action="send", object_type="survey", object_id=str(survey.id), recipient_count=1)
                    except Exception:
                        delivery.status = "failed"
                        await session.commit()


async def load_delivery(session, delivery_id: int, recipient_id: int):
    delivery = await session.get(SurveyDelivery, delivery_id)
    if not delivery or int(delivery.recipient_id) != int(recipient_id) or delivery.status == "completed":
        return None
    survey = await session.get(Survey, delivery.survey_id)
    questions = (await session.scalars(select(SurveyQuestion).where(SurveyQuestion.survey_id == delivery.survey_id).order_by(SurveyQuestion.position))).all()
    return delivery, survey, questions
