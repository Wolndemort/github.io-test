import asyncio
from datetime import timedelta, datetime
from loguru import logger
from redis.asyncio import Redis

from handlers.states import SuperAdminStates
from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import Club, Student
from database.constants import DEFAULT_CLUB_SETTINGS
from config import ADMIN_IDS
from handlers.states import AddClub

router = Router()


@router.message(Command("super"))
@router.callback_query(F.data == "super") # Теперь ловит и кнопку "Назад"
async def super_admin_main(event: types.Message | types.CallbackQuery, is_super_adm: bool):
    # 1. Проверка прав из мидлваря
    if not is_super_adm:
        if isinstance(event, types.CallbackQuery):
            await event.answer("❌ Доступ запрещен", show_alert=True)
        return

    # 2. Если это колбэк — убираем часики
    if isinstance(event, types.CallbackQuery):
        await event.answer()

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Добавить клуб", callback_data="add_new_club"))
    builder.row(types.InlineKeyboardButton(text="📋 Список всех клубов", callback_data="list_clubs"))
    builder.row(types.InlineKeyboardButton(text="💳 Продлить подписку клуба", callback_data="extend_club_sub"))
    builder.row(types.InlineKeyboardButton(text="📊 Общая статистика системы", callback_data="system_stats"))
    builder.row(types.InlineKeyboardButton(text="📢 Рассылка ВСЕМ владельцам", callback_data="broadcast_to_owners"))
    builder.row(types.InlineKeyboardButton(text="🔄 Обновить конфиг (Redis)", callback_data="reload_cache"))

    text = (
        "👑 <b>ПАНЕЛЬ ГЛАВНОГО АДМИНИСТРАТОРА (SaaS)</b>\n\n"
        "Здесь вы управляете франшизами, подписками и общей конфигурацией системы."
    )

    # 3. Логика вывода: если кнопка — редактируем старое, если команда — шлем новое
    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await event.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "add_new_club", F.from_user.id.in_(ADMIN_IDS))
async def start_add_club(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AddClub.waiting_for_name)
    await callback.message.answer("Введите название клуба (например: 'FitGym Майкоп'):")
    await callback.answer()

# 2. Получаем название -> Переходим к OWNER_ID


@router.message(AddClub.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    # ИСПРАВИЛ: Переход на правильный стейт ожидания ID
    await state.set_state(AddClub.waiting_for_owner_id)
    await message.answer("👤 Теперь введите <b>Telegram ID владельца</b> клуба:", parse_mode="HTML")

# 3. Получаем Owner ID -> Переходим к TOKEN


@router.message(AddClub.waiting_for_owner_id)
async def process_owner_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ Введите числовой ID!")

    await state.update_data(owner_id=int(message.text))
    # ИСПРАВИЛ: Теперь идем к токену
    await state.set_state(AddClub.waiting_for_token)
    await message.answer("🤖 Теперь пришли <b>API Token</b> бота из @BotFather:", parse_mode="HTML")

# 4. Финал: Сохраняем всё в базу


@router.message(AddClub.waiting_for_token)
async def process_token(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    club_name = data['name']
    owner_id = data['owner_id']
    bot_token = message.text.strip()

    try:
        # Валидация токена (Защита main.py от падения)
        try:
            temp_bot = Bot(token=bot_token)
            await temp_bot.get_me()
            await temp_bot.session.close()
        except Exception:
            return await message.answer("❌ Токен невалиден! Проверьте данные.")

        new_club = Club(
            name=club_name,
            bot_token=bot_token,
            owner_id=owner_id,
            club_settings=DEFAULT_CLUB_SETTINGS.copy(),
            is_active=True
        )

        session.add(new_club)
        await session.commit()
        await session.refresh(new_club)

        await message.answer(
            f"✅ <b>Клуб успешно создан!</b>\n\n"
            f"🆔 ID в SaaS: <code>{new_club.id}</code>\n"
            f"🏢 Название: <code>{club_name}</code>\n"
            f"👤 Владелец ID: <code>{owner_id}</code>\n\n"
            f"🚀 Перезапустите систему для активации бота.",
            parse_mode="HTML"
        )
        await state.clear()

    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка регистрации клуба: {e}")
        await message.answer(f"❌ Ошибка БД: {str(e)}")
        await state.clear()


@router.callback_query(F.data == "extend_club_sub")
async def list_for_extend(
    callback: types.CallbackQuery,
    session: AsyncSession,
    is_super_adm: bool
):
    # 1. Сразу отвечаем на колбэк, чтобы убрать "часики" (зависание)
    await callback.answer()

    # 2. Проверка прав через флаг из мидлваря
    if not is_super_adm:
        return await callback.message.answer("❌ У вас нет прав администратора.")

    try:
        # 3. Тянем все клубы
        result = await session.execute(select(Club).order_by(Club.name))
        clubs = result.scalars().all()

        if not clubs:
            return await callback.message.edit_text("❌ В системе еще нет зарегистрированных клубов.")

        builder = InlineKeyboardBuilder()
        now = datetime.now()

        for c in clubs:
            # Проверяем дату
            if c.expire_date:
                expire_str = c.expire_date.strftime('%d.%m.%Y')
                # Сравниваем даты (убедись, что в БД тип DateTime)
                status_icon = "🔴" if c.expire_date < now else "⏳"
            else:
                expire_str = "Без срока"
                status_icon = "⚪️"

            builder.row(types.InlineKeyboardButton(
                text=f"{status_icon} {c.name} ({expire_str})",
                callback_data=f"do_extend_{c.id}")
            )

        builder.row(types.InlineKeyboardButton(text="🔙 Назад", callback_data="super"))

        await callback.message.edit_text(
            "💳 <b>Управление подписками клубов</b>\n\n"
            "Выберите клуб, чтобы продлить доступ на <b>30 дней</b>:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        # Если что-то пойдет не так, мы увидим ошибку в консоли, а не зависание
        print(f"Ошибка в хендлере extend_club_sub: {e}")
        await callback.message.answer("Произошла ошибка при получении списка клубов.")


@router.callback_query(F.data.startswith("do_extend_"))
async def process_extend(
        callback: types.CallbackQuery,
        session: AsyncSession,
        is_super_adm: bool  # Получаем из мидлваря
):
    # 1. Сразу убираем часики
    await callback.answer()

    # 2. Проверка прав
    if not is_super_adm:
        return await callback.message.answer("❌ У вас нет прав для этой операции.")

    # 3. Парсим ID клуба
    try:
        club_id = int(callback.data.split("_")[-1])
        club = await session.get(Club, club_id)

        if not club:
            return await callback.message.answer("❌ Клуб не найден в базе.")

        # 4. Логика продления
        now = datetime.now()

        # Если даты нет или она в прошлом — считаем от "сейчас"
        if not club.expire_date or club.expire_date < now:
            club.expire_date = now + timedelta(days=30)
        else:
            # Если подписка еще активна — плюсуем к остатку
            club.expire_date += timedelta(days=30)

        await session.commit()

        # 5. Уведомление (всплывающее)
        await callback.answer(f"✅ Клуб {club.name} продлен до {club.expire_date.strftime('%d.%m.%Y')}!",
                              show_alert=True)

        # 6. Возврат в список (ОБЯЗАТЕЛЬНО передаем все аргументы, которые ждет list_for_extend)
        await list_for_extend(callback, session, is_super_adm)

    except Exception as e:
        print(f"Ошибка при продлении: {e}")
        await callback.message.answer("⚠️ Произошла ошибка при сохранении данных.")


@router.message(SuperAdminStates.waiting_for_broadcast_text)
async def process_broadcast_to_owners(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession
):
    # 1. Получаем список ВСЕХ уникальных владельцев клубов
    # (Используем set или DISTINCT в SQL, чтобы не спамить одному человеку дважды)
    stmt = select(Club.owner_id).where(Club.owner_id.isnot(None))
    result = await session.execute(stmt)
    # Множество (set) уберет дубликаты
    owner_ids = set(result.scalars().all())

    if not owner_ids:
        return await message.answer("📭 В системе еще нет владельцев клубов.")

    await message.answer(f"🚀 Начинаю рассылку для {len(owner_ids)} владельцев...")

    count = 0
    for oid in owner_ids:
        try:
            # Копируем сообщение (текст, фото, видео — всё подхватит)
            await message.copy_to(chat_id=oid)
            count += 1
            # Небольшая пауза для защиты от Flood Limit Telegram
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.warning(f"Не удалось отправить владельцу {oid}: {e}")

    await message.answer(f"✅ Рассылка завершена!\nДоставлено: <b>{count}</b> из <b>{len(owner_ids)}</b>",
                         parse_mode="HTML")
    await state.clear()


@router.message(SuperAdminStates.waiting_for_broadcast_text, F.from_user.id.in_(ADMIN_IDS))
async def send_broadcast(message: Message, state: FSMContext, session: AsyncSession):
    broadcast_text = message.text

    # 1. 🛡️ ИЗОЛЯЦИЯ: Только реальные ID и без дублей
    stmt = select(Club.owner_id).where(Club.owner_id.isnot(None)).distinct()
    result = await session.execute(stmt)
    owner_ids = result.scalars().all()

    if not owner_ids:
        return await message.answer("📭 В системе еще нет владельцев.")

    await message.answer(f"🚀 Начинаю рассылку для {len(owner_ids)} владельцев...")

    count = 0
    for o_id in owner_ids:
        try:
            # Используем copy_to, если вдруг ты захочешь отправить фото или видео
            # Или оставляем send_message, как у тебя
            await message.bot.send_message(
                o_id,
                f"📢 <b>УВЕДОМЛЕНИЕ ОТ ПЛАТФОРМЫ:</b>\n\n{broadcast_text}",
                parse_mode="HTML"
            )
            count += 1
            # Защита от лимитов Telegram (Flood)
            await asyncio.sleep(0.05)
        except Exception:
            continue

    await message.answer(
        f"✅ Рассылка завершена!\nДоставлено: <b>{count}</b> из <b>{len(owner_ids)}</b> владельцам.",
        parse_mode="HTML"
    )
    await state.clear()


@router.callback_query(F.data == "system_stats", F.from_user.id.in_(ADMIN_IDS))
async def system_stats_handler(callback: types.CallbackQuery, session: AsyncSession):
    # Используем твой быстрый метод подсчета через func.count
    clubs_count = (await session.execute(select(func.count(Club.id)))).scalar() or 0
    students_count = (await session.execute(select(func.count(Student.id)))).scalar() or 0
    active_clubs = (await session.execute(select(func.count(Club.id)).where(Club.is_active == True))).scalar() or 0

    text = (
        "📊 <b>ГЛОБАЛЬНАЯ СТАТИСТИКА SaaS</b>\n\n"
        f"🏢 Всего клубов в системе: <b>{clubs_count}</b>\n"
        f"✅ Из них активно узлов: <b>{active_clubs}</b>\n"
        f"🥋 Общее кол-во атлетов: <b>{students_count}</b>\n\n"
        f"<i>📡 Все системы мониторинга в норме.</i>"
    )

    # Добавляем кнопку возврата, чтобы не "залипать" на статистике
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад в Super-Panel", callback_data="super"))

    await callback.message.edit_text(
        text=text,
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "list_clubs")
async def handle_list_clubs(
    callback: types.CallbackQuery,
    session: AsyncSession,
    is_super_adm: bool,  # Прилетает из мидлваря
    club: Club           # Прилетает из мидлваря (текущий клуб бота)
):
    # Проверка прав (если список клубов только для админов)
    if not is_super_adm:
        await callback.answer("У вас нет прав для просмотра всех клубов.", show_alert=True)
        return

    # Получаем все клубы из базы
    result = await session.execute(select(Club))
    clubs = result.scalars().all()

    if not clubs:
        await callback.message.answer("Список клубов пуст.")
        return

    # Формируем сообщение
    text = "<b>Список всех зарегистрированных клубов:</b>\n\n"
    for i, c in enumerate(clubs, 1):
        text += f"{i}. {c.name} (ID: {c.id})\n"

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "reload_cache")
async def reload_system_cache(callback: types.CallbackQuery, redis: Redis, is_super_adm: bool):
    if not is_super_adm:
        return await callback.answer("Нет прав", show_alert=True)

    # Очищаем все ключи, начинающиеся с club_config:
    # Внимание: на большом количестве данных лучше использовать SCAN,
    # но для начала можно просто удалить текущий
    bot_token = callback.bot.token
    await redis.delete(f"club_config:{bot_token}")

    await callback.answer("✅ Конфигурация текущего бота обновлена из БД!", show_alert=True)
