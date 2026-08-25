import asyncio
import copy
from datetime import timedelta, datetime
from aiogram.exceptions import TelegramUnauthorizedError, TelegramNetworkError
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.orm.attributes import flag_modified
from handlers.states import SuperAdminStates
from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import Club, Student, ClubStaff
from database.constants import DEFAULT_CLUB_SETTINGS
from config import ADMIN_IDS
from config import BASE_URL
from handlers.states import AddClub
from services.audit import audit_event
from services.bot_registry import register_bot

router = Router()


def merge_default_club_settings(current_settings: dict, default_settings: dict = DEFAULT_CLUB_SETTINGS) -> tuple[dict, bool]:
    merged = copy.deepcopy(current_settings or {})
    is_modified = False

    for section_key, section_value in default_settings.items():
        if section_key not in merged:
            merged[section_key] = copy.deepcopy(section_value)
            is_modified = True
            continue

        if isinstance(section_value, dict) and isinstance(merged[section_key], dict):
            for field_key, field_value in section_value.items():
                if field_key not in merged[section_key]:
                    if field_key in ["yookassa_shop_id", "yookassa_secret_key", "password", "base_url"]:
                        continue
                    merged[section_key][field_key] = copy.deepcopy(field_value)
                    is_modified = True

    return merged, is_modified


@router.message(Command("super"), F.from_user.id.in_(ADMIN_IDS))
@router.callback_query(F.data == "super", F.from_user.id.in_(ADMIN_IDS))
async def super_admin_main(
        event: types.Message | types.CallbackQuery,
        is_super_admin: bool,
        state: FSMContext
):
    await state.clear()

    # Если это нажатие кнопки — убираем часики
    if isinstance(event, types.CallbackQuery):
        await event.answer()

    builder = InlineKeyboardBuilder()

    # --- НАША НОВАЯ КНОПКА (Переход в SqlAdmin на /master-dashboard) ---
    builder.row(types.InlineKeyboardButton(
        text="🖥 Управление всеми клубами (SqlAdmin)",
        url="https://speedycrm.ru/master-dashboard"  # Откроет список франшиз
    ))

    # --- Остальные кнопки управления SaaS ---
    builder.row(types.InlineKeyboardButton(text="➕ Добавить клуб", callback_data="add_new_club"))
    builder.row(types.InlineKeyboardButton(text="📋 Список всех клубов", callback_data="list_clubs"))
    builder.row(types.InlineKeyboardButton(text="📜 Аудит", callback_data="super_audit"))
    builder.row(types.InlineKeyboardButton(text="👔 Добавить сотрудника клубу", callback_data="super_staff_add"))
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


@router.callback_query(F.data == "super_staff_add", F.from_user.id.in_(ADMIN_IDS))
async def super_staff_add_start(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext):
    clubs = (await session.execute(select(Club).order_by(Club.id))).scalars().all()
    kb = InlineKeyboardBuilder()
    for club in clubs:
        kb.button(text=f"{club.id}: {club.name}", callback_data=f"super_staff_club_{club.id}")
    kb.adjust(1)
    kb.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="super"))
    await callback.message.edit_text("Выберите клуб для сотрудника:", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data == "super_audit", F.from_user.id.in_(ADMIN_IDS))
async def super_audit_start(callback: types.CallbackQuery, session: AsyncSession):
    clubs = (await session.execute(select(Club).order_by(Club.id))).scalars().all()
    kb = InlineKeyboardBuilder()
    for club in clubs:
        kb.button(text=f"{club.id}: {club.name}", callback_data=f"super_audit_club_{club.id}")
    kb.adjust(1)
    kb.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="super"))
    await callback.message.edit_text("Выберите клуб для просмотра аудита:", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("super_audit_club_"), F.from_user.id.in_(ADMIN_IDS))
async def super_audit_choose_club(callback: types.CallbackQuery):
    club_id = int(callback.data.rsplit("_", 1)[1])
    await callback.message.answer(
        f"Откройте аудит клуба <code>{club_id}</code> в WebApp:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📜 Открыть аудит", web_app=types.WebAppInfo(url=f"https://{club_id}.speedycrm.ru/webapp/admin-audit?club_id={club_id}"))],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="super_audit")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("super_staff_club_"), F.from_user.id.in_(ADMIN_IDS))
async def super_staff_choose_club(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(staff_club_id=int(callback.data.rsplit("_", 1)[1]))
    await state.set_state(SuperAdminStates.waiting_for_staff_telegram_id)
    await callback.message.answer(
        "Введите Telegram ID сотрудника:",
        reply_markup=InlineKeyboardBuilder().row(
            types.InlineKeyboardButton(text="⬅️ Назад", callback_data="super")
        ).as_markup()
    )
    await callback.answer()


@router.message(SuperAdminStates.waiting_for_staff_telegram_id, F.from_user.id.in_(ADMIN_IDS))
async def super_staff_id(message: types.Message, state: FSMContext):
    if not (message.text or "").strip().isdigit():
        return await message.answer("ID должен состоять только из цифр.")
    await state.update_data(staff_telegram_id=int(message.text.strip()))
    await state.set_state(SuperAdminStates.waiting_for_staff_role)
    kb = InlineKeyboardBuilder()
    kb.button(text="☕ Бариста", callback_data="super_staff_role_cashier")
    kb.button(text="🥋 Тренер", callback_data="super_staff_role_coach")
    kb.button(text="📋 Менеджер", callback_data="super_staff_role_manager")
    kb.adjust(1)
    kb.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="super"))
    await message.answer("Выберите роль сотрудника:", reply_markup=kb.as_markup())


@router.callback_query(SuperAdminStates.waiting_for_staff_role, F.data.startswith("super_staff_role_"), F.from_user.id.in_(ADMIN_IDS))
async def super_staff_role(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    role = callback.data.removeprefix("super_staff_role_")
    data = await state.get_data()
    staff = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == data["staff_club_id"], ClubStaff.telegram_id == data["staff_telegram_id"]))).scalar_one_or_none()
    try:
        telegram_chat = await callback.bot.get_chat(data["staff_telegram_id"])
    except Exception:
        telegram_chat = None
    staff_name = getattr(telegram_chat, "full_name", None)
    staff_username = getattr(telegram_chat, "username", None)
    if staff:
        staff.role = role; staff.is_active = True
        staff.full_name = staff.full_name or staff_name
    else:
        session.add(ClubStaff(club_id=data["staff_club_id"], telegram_id=data["staff_telegram_id"], role=role, full_name=staff_name))
    await session.commit(); await state.clear()
    await callback.message.answer(f"✅ Сотрудник добавлен в клуб. Роль: {role}\nИмя: {staff_name or 'неизвестно'}\nUsername: @{staff_username if staff_username else 'нет'}")
    try:
        club = await session.get(Club, data["staff_club_id"])
        await callback.bot.send_message(data["staff_telegram_id"], f"🎉 <b>Вы приняты в команду клуба {club.name if club else ''}!</b>\nВаша должность: <b>{role}</b>\nВам доступен кабинет сотрудника.", parse_mode="HTML")
    except Exception:
        logger.warning("Не удалось уведомить сотрудника о назначении: %s", data["staff_telegram_id"])
    await callback.answer()
    audit_event(
        "super_staff_saved",
        club_id=data["staff_club_id"],
        action="create" if not staff else "update",
        object_type="staff",
        object_id=getattr(staff, "id", None) or data["staff_telegram_id"],
        location="super/staff",
        actor_user_id=callback.from_user.id,
        actor_role="super_admin",
        actor_name=callback.from_user.full_name,
        staff_telegram_id=data["staff_telegram_id"],
        role=role,
    )


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
            club_settings=copy.deepcopy(DEFAULT_CLUB_SETTINGS),
            subscription_expire_at=datetime.now() + timedelta(days=30)
        )

        session.add(new_club)
        await session.commit()
        await session.refresh(new_club)

        webhook_url = f"{BASE_URL}/webhook/bot/{bot_token}"
        try:
            await register_bot(bot_token, webhook_url)
            bot_status = "🤖 Бот подключён к текущему процессу и вебхук обновлён."
        except Exception as bot_err:
            logger.error(f"Не удалось сразу зарегистрировать бот для клуба {new_club.id}: {bot_err}")
            bot_status = "⚠️ Клуб создан, но бот не подхватился сразу. Перезапуск не нужен — попробуйте позже или проверьте токен."

        await message.answer(
            f"✅ <b>Клуб успешно создан!</b>\n\n"
            f"🆔 ID в SaaS: <code>{new_club.id}</code>\n"
            f"🏢 Название: <code>{club_name}</code>\n"
            f"{bot_status}",
            parse_mode="HTML"
        )
        audit_event(
            "club_created",
            club_id=new_club.id,
            action="create",
            object_type="club",
            object_id=new_club.id,
            location="super/create_club",
            actor_user_id=message.from_user.id,
            actor_role="super_admin",
            actor_name=message.from_user.full_name,
            name=club_name,
            owner_id=owner_id,
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
        logger.exception("Ошибка в хендлере extend_club_sub")
        await callback.message.answer("Произошла ошибка при получении списка клубов.")


@router.callback_query(F.data.startswith("do_extend_"))
async def process_extend(
        callback: types.CallbackQuery,
        session: AsyncSession,
        is_super_admin: bool,
        redis: Redis | None = None
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
        if redis is not None and club.bot_token:
            await redis.delete(f"club_config:{club.bot_token}")
            logger.info(f"Супер-админ продлил клуб {club.id}, кэш сброшен.")
        audit_event(
            "club_subscription_extended",
            club_id=club.id,
            action="update",
            object_type="club_subscription",
            object_id=club.id,
            location="super/extend_sub",
            actor_user_id=callback.from_user.id,
            actor_role="super_admin",
            actor_name=callback.from_user.full_name,
            previous_expire=current_expire.isoformat() if current_expire else None,
            new_expire=club.subscription_expire_at.isoformat() if club.subscription_expire_at else None,
            days=30,
        )

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
    active_clubs = (await session.execute(select(func.count(Club.id)).where(Club.subscription_expire_at >
                                                                            datetime.now()))).scalar() or 0
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
        return await callback.answer("У вас нет прав для просмотра всех клубов.", show_alert=True)
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
        session: AsyncSession,
        is_super_admin: bool
):
    """
    Хендлер суперадмина: синхронизирует структуру JSONB настроек всех клубов с константой
    и полностью сбрасывает кэш конфигураций в Redis.
    """
    if not is_super_admin:
        return await callback.answer("У вас нет прав суперадмина ⛔", show_alert=True)

    try:
        # 1. Вытаскиваем абсолютно все существующие клубы из базы данных
        result = await session.execute(select(Club))
        all_clubs = result.scalars().all()

        updated_clubs_count = 0

        for club in all_clubs:
            current_settings, is_modified = merge_default_club_settings(club.club_settings or {})

            # Сохраняем изменения в базу только если реально нашли и добавили новые поля структуры
            if is_modified:
                club.club_settings = current_settings
                flag_modified(club, "club_settings")
                session.add(club)
                updated_clubs_count += 1

        if updated_clubs_count > 0:
            await session.commit()
            # Очищаем кэш текущей сессии SQLAlchemy, чтобы принудительно перечитать свежий JSON
            session.expire_all()
        audit_event(
            "system_config_reloaded",
            club_id=None,
            action="update",
            object_type="system_config",
            object_id="all_clubs",
            location="super/reload_cache",
            actor_user_id=callback.from_user.id,
            actor_role="super_admin",
            actor_name=callback.from_user.full_name,
            updated_clubs_count=updated_clubs_count,
            cache_deleted=True,
        )

    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка при мердже дефолтных настроек в СУБД: {e}")
        return await callback.answer(f"Ошибка БД: {e}", show_alert=True)

    # 2. Очищаем кэш всех ботов в Redis, чтобы новые поля моментально стали доступны
    cursor = 0
    deleted_count = 0
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match="club_config:*", count=100)
        if keys:
            await redis.delete(*keys)
            deleted_count += len(keys)
        if cursor == 0:
            break

    await callback.answer(
        f"🚀 Структура JSONB синхронизирована!\n\n"
        f"Добавлены новые поля в {updated_clubs_count} клубов.\n"
        f"Боевые настройки не задеты. Кэш {deleted_count} ботов сброшен.",
        show_alert=True
    )
