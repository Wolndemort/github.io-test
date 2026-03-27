import asyncio
from datetime import timedelta, datetime
from loguru import logger
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


@router.message(Command("super"), F.from_user.id.in_(ADMIN_IDS))
async def super_admin_main(message: types.Message):
    builder = InlineKeyboardBuilder()

    # 1. Основное управление клубами
    builder.row(types.InlineKeyboardButton(text="➕ Добавить клуб", callback_data="add_new_club"))
    builder.row(types.InlineKeyboardButton(text="📋 Список всех клубов", callback_data="list_clubs"))

    # 2. Управление биллингом (чтобы отключать неплательщиков)
    builder.row(types.InlineKeyboardButton(text="💳 Продлить подписку клуба", callback_data="extend_club_sub"))

    # 3. Техническая часть
    builder.row(types.InlineKeyboardButton(text="📊 Общая статистика системы", callback_data="system_stats"))
    builder.row(types.InlineKeyboardButton(text="📢 Рассылка ВСЕМ владельцам", callback_data="broadcast_to_owners"))

    await message.answer(
        text="👑 <b>ПАНЕЛЬ ГЛАВНОГО АДМИНИСТРАТОРА (SaaS)</b>\n\n"
             "Здесь вы управляете франшизами, подписками и общей конфигурацией системы.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


# 2. Начало диалога добавления
# 1. Начало
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


@router.callback_query(F.data == "extend_club_sub", F.from_user.id.in_(ADMIN_IDS))
async def list_for_extend(callback: types.CallbackQuery, session: AsyncSession):
    # 1. Тянем все клубы
    result = await session.execute(select(Club).order_by(Club.name))
    clubs = result.scalars().all()

    if not clubs:
        return await callback.answer("❌ В системе еще нет зарегистрированных клубов.", show_alert=True)

    builder = InlineKeyboardBuilder()
    for c in clubs:
        # 🛡️ БЕЗОПАСНО: Сначала проверяем наличие даты, потом форматируем
        if c.expire_date:
            expire_str = c.expire_date.strftime('%d.%m.%Y')
            # Если подписка уже кончилась — помечаем красным
            status_icon = "🔴" if c.expire_date < datetime.now() else "⏳"
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


@router.callback_query(F.data.startswith("do_extend_"), F.from_user.id.in_(ADMIN_IDS))
async def process_extend(callback: types.CallbackQuery, session: AsyncSession):
    club_id = int(callback.data.split("_")[-1])
    club = await session.get(Club, club_id)

    # Логика: если дата уже прошла, продлеваем от сегодня. Если еще нет — плюсуем к текущей.
    now = datetime.now()
    if not club.expire_date or club.expire_date < now:
        club.expire_date = now + timedelta(days=30)
    else:
        club.expire_date += timedelta(days=30)

    await session.commit()
    await callback.answer(f"✅ Клуб {club.name} продлен!", show_alert=True)
    await list_for_extend(callback, session)  # Возвращаемся к списку


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
