from __future__ import annotations

import copy

from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from database.db import Club
from handlers.states import YooKassaSetupStates

router = Router()


@router.callback_query(lambda c: c.data == "admin_setup_yookassa")
async def start_yookassa_setup(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(YooKassaSetupStates.waiting_for_shop_id)

    cancel_kb = InlineKeyboardBuilder()
    cancel_kb.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_settings"))

    await callback.message.edit_text(
        "📥 <b>Настройка интеграции с ЮKassa</b>\n\n"
        "Введите ваш <b>Shop ID</b> (Идентификатор магазина).\n"
        "Вы можете найти его в личном кабинете ЮKassa вверху страницы (обычно состоит только из цифр).",
        reply_markup=cancel_kb.as_markup(),
        parse_mode="HTML"
    )


@router.message(YooKassaSetupStates.waiting_for_shop_id)
async def process_shop_id(message: types.Message, state: FSMContext):
    shop_id = message.text.strip()

    if not shop_id.isdigit():
        return await message.answer("⚠️ Ошибка! Shop ID должен состоять только из цифр. Попробуйте еще раз:")

    await state.update_data(shop_id=shop_id)
    await state.set_state(YooKassaSetupStates.waiting_for_secret_key)

    await message.answer(
        "🔑 Теперь введите ваш <b>Секретный ключ</b> (Секрет).\n\n"
        "Его можно сгенерировать в ЛК ЮKassa в разделе <i>«Интеграция» -> «Ключи API»</i>.\n"
        "Он начинается на <code>test_...</code> (для тестового режима) или <code>live_...</code> (для боевого).",
        parse_mode="HTML"
    )


@router.message(YooKassaSetupStates.waiting_for_secret_key)
async def process_secret_key(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    club_id: int,
):
    secret_key = message.text.strip()

    if not (secret_key.startswith("test_") or secret_key.startswith("live_")):
        return await message.answer(
            "⚠️ Ошибка! Неверный формат ключа. Он должен начинаться с <code>test_</code> или <code>live_</code>.\n"
            "Попробуйте ввести ключ заново:",
            parse_mode="HTML"
        )

    user_data = await state.get_data()
    shop_id = user_data["shop_id"]

    await state.clear()

    result = await session.execute(
        select(Club)
        .where(Club.id == club_id)
        .with_for_update()
    )
    club = result.scalar_one_or_none()

    if club:
        current_settings = copy.deepcopy(club.club_settings) if club.club_settings else {}

        if "payments" not in current_settings:
            current_settings["payments"] = {}

        current_settings["payments"]["provider"] = "yookassa"
        current_settings["payments"]["yookassa_shop_id"] = shop_id
        current_settings["payments"]["yookassa_secret_key"] = secret_key

        if "features" not in current_settings:
            current_settings["features"] = {}
        current_settings["features"]["online_payments"] = True

        club.club_settings = current_settings
        flag_modified(club, "club_settings")
        await session.commit()

        back_kb = InlineKeyboardBuilder()
        back_kb.row(types.InlineKeyboardButton(text="⚙️ Вернуться в настройки", callback_data="admin_settings"))

        await message.answer(
            "✅ <b>Интеграция успешно настроена!</b>\n\n"
            f"<b>Shop ID:</b> <code>{shop_id}</code>\n"
            f"<b>Ключ:</b> <code>{secret_key[:8]}...****</code>\n\n"
            "Теперь ваши клиенты смогут привязывать карты и оплачивать подписки онлайн.",
            reply_markup=back_kb.as_markup(),
            parse_mode="HTML"
        )
    else:
        await message.answer("🚨 Произошла критическая ошибка: Клуб не найден в базе данных.")
