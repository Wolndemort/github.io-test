from __future__ import annotations

from datetime import datetime

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import SurveyAnswer
from handlers.states import SurveyStates
from services.surveys import load_delivery, _question_keyboard

router = Router()


async def _send_question(callback: types.CallbackQuery, delivery, questions, session: AsyncSession, state: FSMContext | None = None):
    answered = {row.question_id for row in (await session.scalars(select(SurveyAnswer).where(SurveyAnswer.delivery_id == delivery.id))).all()}
    question = next((item for item in questions if item.id not in answered), None)
    if not question:
        delivery.status = "completed"
        delivery.completed_at = datetime.utcnow()
        await session.commit()
        await callback.message.answer("Спасибо за обратную связь! Мы обязательно её изучим 🙏")
        return
    if delivery.status == "sent":
        delivery.status = "started"
        delivery.started_at = datetime.utcnow()
    await session.commit()
    if question.kind == "text":
        if state:
            await state.update_data(delivery_id=delivery.id, question_id=question.id)
            await state.set_state(SurveyStates.waiting_for_text)
        await callback.message.answer(question.text)
    else:
        await callback.message.answer(question.text, reply_markup=_question_keyboard(delivery.id, question))


@router.callback_query(F.data.startswith("survey_start:"))
async def survey_start(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext):
    delivery_id = int(callback.data.split(":", 1)[1])
    loaded = await load_delivery(session, delivery_id, callback.from_user.id)
    if not loaded:
        await callback.answer("Опрос уже завершён или недоступен", show_alert=True)
        return
    delivery, _survey, questions = loaded
    await callback.answer()
    await _send_question(callback, delivery, questions, session, state)


@router.callback_query(F.data.startswith("survey_answer:"))
async def survey_answer(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext):
    _prefix, raw_delivery, raw_question, raw_option = callback.data.split(":", 3)
    loaded = await load_delivery(session, int(raw_delivery), callback.from_user.id)
    if not loaded:
        await callback.answer("Опрос уже завершён или недоступен", show_alert=True)
        return
    delivery, _survey, questions = loaded
    question = next((item for item in questions if item.id == int(raw_question)), None)
    if not question or question.kind == "text":
        await callback.answer("Этот вопрос уже недоступен", show_alert=True)
        return
    options = question.options or []
    option_index = int(raw_option)
    if option_index < 0 or option_index >= len(options):
        await callback.answer("Вариант ответа недоступен", show_alert=True)
        return
    exists = await session.scalar(select(SurveyAnswer.id).where(SurveyAnswer.delivery_id == delivery.id, SurveyAnswer.question_id == question.id))
    if not exists:
        session.add(SurveyAnswer(delivery_id=delivery.id, survey_id=delivery.survey_id, question_id=question.id, student_id=delivery.student_id, recipient_id=delivery.recipient_id, value=str(options[option_index]), value_json={"option_index": option_index}))
        await session.commit()
    await callback.answer("Ответ сохранён")
    await _send_question(callback, delivery, questions, session, state)


@router.message(SurveyStates.waiting_for_text)
async def survey_text_answer(message: types.Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    loaded = await load_delivery(session, int(data.get("delivery_id", 0)), message.from_user.id)
    if not loaded:
        await state.clear()
        return await message.answer("Опрос уже завершён или недоступен.")
    delivery, _survey, questions = loaded
    question = next((item for item in questions if item.id == int(data.get("question_id", 0))), None)
    if question:
        session.add(SurveyAnswer(delivery_id=delivery.id, survey_id=delivery.survey_id, question_id=question.id, student_id=delivery.student_id, recipient_id=delivery.recipient_id, value=(message.text or "")[:4000], value_json={}))
        await session.commit()
    await state.clear()
    await message.answer("Ответ сохранён")
    answered = {row.question_id for row in (await session.scalars(select(SurveyAnswer).where(SurveyAnswer.delivery_id == delivery.id))).all()}
    next_question = next((item for item in questions if item.id not in answered), None)
    if not next_question:
        delivery.status = "completed"
        delivery.completed_at = datetime.utcnow()
        await session.commit()
        return await message.answer("Спасибо за обратную связь! Мы обязательно её изучим 🙏")
    if next_question.kind == "text":
        await state.update_data(delivery_id=delivery.id, question_id=next_question.id)
        await state.set_state(SurveyStates.waiting_for_text)
        return await message.answer(next_question.text)
    await message.answer(next_question.text, reply_markup=_question_keyboard(delivery.id, next_question))
