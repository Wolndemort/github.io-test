import asyncio
from datetime import timedelta, datetime
from aiogram.exceptions import TelegramUnauthorizedError, TelegramNetworkError
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy import update
from handlers.states import SuperAdminStates
from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import Club, Student
from database.constants import DEFAULT_CLUB_SETTINGS
from config import ADMIN_IDS
from handlers.states import AddClub

router = Router()


@router.message(Command("super"), F.from_user.id.in_(ADMIN_IDS))
@router.callback_query(F.data == "super", F.from_user.id.in_(ADMIN_IDS))
async def super_admin_main(
    event: types.Message | types.CallbackQuery,
    is_super_admin: bool  # <--- ВОТ ЭТОГО НЕ ХВАТАЛО!
):
    # Если это нажатие кнопки — убираем часики
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

    # 1. Валидация токена
    try:
        # Используем .context(), чтобы aiogram сам ПРАВИЛЬНО закрыл сессию
        async with Bot(token=bot_token).context() as temp_bot:
            await temp_bot.get_me()
    except TelegramUnauthorizedError:
        return await message.answer("❌ Токен невалиден! Проверьте данные в @BotFather.")
    except TelegramNetworkError as e:
        # Если здесь вылетает Timeout - значит сервер всё еще в бане/без сети
        logger.error(f"Сетевая ошибка при проверке токена: {e}")
        return await message.answer(f"🌐 Ошибка сети на сервере: {e}\nСкорее всего, Telegram временно ограничил ваш IP.")
    except Exception as e:
        logger.error(f"Неизвестная ошибка валидации: {e}")
        return await message.answer(f"⚠️ Ошибка при проверке: {e}")

    # 2. Сохранение в БД
    try:
        new_club = Club(
            name=club_name,
            bot_token=bot_token,
            owner_id=owner_id,
            club_settings=DEFAULT_CLUB_SETTINGS.copy(),
            subscription_expire_at=datetime.now() + timedelta(days=30)
        )

        session.add(new_club)
        await session.commit()
        await session.refresh(new_club)

        await message.answer(
            f"✅ <b>Клуб успешно создан!</b>\n\n"
            f"🆔 ID в SaaS: <code>{new_club.id}</code>\n"
            f"🏢 Название: <code>{club_name}</code>\n"
            f"🚀 Перезапустите систему для активации бота.",
            parse_mode="HTML"
        )
        await state.clear()

    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка записи в БД: {e}")
        # Если здесь будет ошибка "timestamp = boolean", мы её увидим в логах
        await message.answer(f"❌ Ошибка БД: {str(e)}")


@router.callback_query(F.data == "extend_club_sub")
async def list_for_extend(
    callback: types.CallbackQuery,
    session: AsyncSession,
    is_super_admin: bool
):
    # 1. Сразу отвечаем на колбэк, чтобы убрать "часики" (зависание)
    await callback.answer()

    # 2. Проверка прав через флаг из мидлваря
    if not is_super_admin:
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
            expire_dt = c.subscription_expire_at

            if expire_dt:
                expire_str = expire_dt.strftime('%d.%m.%Y')
                status_icon = "🔴" if expire_dt < now else "⏳"
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
        is_super_admin: bool,
        redis: Redis  # Добавь redis в аргументы (он прилетает из мидлвари)
):
    await callback.answer()
    if not is_super_admin:
        return await callback.message.answer("❌ У вас нет прав.")

    try:
        club_id = int(callback.data.split("_")[-1])
        club = await session.get(Club, club_id)

        if not club:
            return await callback.message.answer("❌ Клуб не найден.")

        now = datetime.now()

        # ВАЖНО: используем правильное имя поля из твоей БД
        current_expire = club.subscription_expire_at

        if not current_expire or current_expire < now:
            club.subscription_expire_at = now + timedelta(days=30)
        else:
            club.subscription_expire_at += timedelta(days=30)

        await session.commit()

        # 🔥 ОЧИСТКА КЭША REDIS
        # После ручного продления нужно снести старый конфиг из кэша
        if club.bot_token:
            await redis.delete(f"club_config:{club.bot_token}")
            logger.info(f"Супер-админ продлил клуб {club.id}, кэш сброшен.")

        await callback.answer(
            f"✅ Продлен до {club.subscription_expire_at.strftime('%d.%m.%Y')}",
            show_alert=True
        )
        await list_for_extend(callback, session, is_super_admin)

    except Exception as e:
        logger.error(f"Ошибка продления: {e}")
        await callback.message.answer("⚠️ Ошибка при сохранении.")


@router.callback_query(F.data == "broadcast_to_owners", F.from_user.id.in_(ADMIN_IDS))
async def start_broadcast_step(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Введите текст рассылки (можно прикрепить фото или видео):")
    await state.set_state(SuperAdminStates.waiting_for_broadcast_text)
    await callback.answer()


@router.message(SuperAdminStates.waiting_for_broadcast_text, F.from_user.id.in_(ADMIN_IDS))
async def process_unified_broadcast(message: types.Message, state: FSMContext, session: AsyncSession):
    stmt = select(Club.owner_id).where(Club.owner_id.isnot(None)).distinct()
    result = await session.execute(stmt)
    owner_ids = result.scalars().all()

    if not owner_ids:
        await state.clear()
        return await message.answer("📭 В системе еще нет владельцев клубов.")

    status_msg = await message.answer(f"🚀 Начинаю рассылку для {len(owner_ids)}владельцев...")
    count = 0
    for oid in owner_ids:
        try:
            await message.copy_to(chat_id=oid)
            count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.warning(f"Ошибка отправки пользователю {oid}: {e}")
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📈 Доставлено: <b>{count}</b>\n"
        f"❌ Ошибки: <b>{len(owner_ids) - count}</b>",
        parse_mode="HTML"
    )
    await state.clear()


@router.callback_query(F.data == "system_stats", F.from_user.id.in_(ADMIN_IDS))
async def system_stats_handler(callback: types.CallbackQuery, session: AsyncSession):
    # Используем твой быстрый метод подсчета через func.count
    clubs_count = (await session.execute(select(func.count(Club.id)))).scalar() or 0
    students_count = (await session.execute(select(func.count(Student.id)))).scalar() or 0
    active_clubs = (await session.execute(select(func.count(Club.id)).where(Club.subscription_expire_at == True))).scalar() or 0

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
    is_super_admin: bool,  # Прилетает из мидлваря
    club: Club           # Прилетает из мидлваря (текущий клуб бота)
):
    # Проверка прав (если список клубов только для админов)
    if not is_super_admin:
        await callback.answer("У вас нет прав для просмотра всех клубов.", show_alert=True)
    await callback.answer()

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
async def reload_all_system_configs(
    callback: types.CallbackQuery,
    redis: Redis,
    session, # Сессия из Middleware
    is_super_admin: bool
):
    if not is_super_admin:
        return await callback.answer("У вас нет прав суперадмина ⛔", show_alert=True)

    # 1. ОБНОВЛЯЕМ ВСЮ ТАБЛИЦУ 'clubs' (Всех ботов системы)
    # Это пропишет твой новый DEFAULT_CLUB_SETTINGS (с кавычками и расписанием)
    # во все строки базы данных.
    try:
        await session.execute(
            update(Club).values(club_settings=DEFAULT_CLUB_SETTINGS)
        )
        await session.commit()
    except Exception as e:
        await session.rollback()
        return await callback.answer(f"Ошибка БД: {e}", show_alert=True)

    # 2. ОЧИЩАЕМ КЭШ ВСЕХ БОТОВ В REDIS
    # Твой Middleware использует ключи 'club_config:ТОКЕН'.
    # Нам нужно удалить ВСЕ такие ключи, чтобы боты пошли в БД за обновой.
    cursor = 0
    deleted_count = 0
    while True:
        # Ищем все ключи конфигов
        cursor, keys = await redis.scan(cursor=cursor, match="club_config:*", count=100)
        if keys:
            await redis.delete(*keys)
            deleted_count += len(keys)
        if cursor == 0:
            break

    await callback.answer(
        f"🚀 Глобальное обновление!\n"
        f"База обновлена. Кэш {deleted_count} ботов сброшен.",
        show_alert=True
    )




