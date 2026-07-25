from __future__ import annotations

from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from loguru import logger

from database.db import Club
from handlers.states import AdminSettings

router = Router()


@router.callback_query(lambda c: c.data == "admin_edit_payments")
async def edit_payments_info(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📝 <b>Редактирование реквизитов</b>\n\n"
        "Введите новый текст.\n"
        "Например: <code>+79001234567 (Иван И.)</code>",
        parse_mode="HTML"
    )
    await state.set_state(AdminSettings.waiting_for_payment_info)
    await callback.answer()


@router.message(AdminSettings.waiting_for_payment_info)
async def save_payment_info(message: types.Message, state: FSMContext, session: AsyncSession, club: Club, redis: Redis):
    new_info = message.text.strip()
    new_settings = dict(club.club_settings)
    if "ui" not in new_settings:
        new_settings["ui"] = {}
    new_settings["ui"]["payment_info"] = new_info

    try:
        await session.execute(
            update(Club)
            .where(Club.id == club.id)
            .values(club_settings=new_settings)
        )
        await session.commit()
        cache_key = f"club_config:{message.bot.token}"
        await redis.delete(cache_key)
        logger.warning(f"!!! БАЗА ОБНОВЛЕНА ДЛЯ КЛУБА {club.id} !!!")
        await message.answer("✅ Готово! Новые реквизиты записаны в БД.")
        await state.clear()
    except Exception as e:
        logger.error(f"ОШИБКА ЗАПИСИ: {e}")
        await session.rollback()
        await message.answer("❌ Ошибка при сохранении.")
