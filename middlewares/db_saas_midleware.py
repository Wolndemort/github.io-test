import json
from datetime import datetime
from aiogram import BaseMiddleware, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from database.db import Club
from redis.asyncio import Redis

SUPER_ADMIN_IDS = [1271717628]


class ClubMiddleware(BaseMiddleware):
    def __init__(self, redis: Redis):
        self.redis = redis
        super().__init__()

    async def __call__(self, handler, event, data):
        if not getattr(event, "from_user", None):
            return await handler(event, data)

        user_id = event.from_user.id
        bot_token = data["bot"].token
        cache_key = f"club_config:{bot_token}"

        # 1. Пытаемся взять из Redis
        cached_club = await self.redis.get(cache_key)
        club_obj = None

        if cached_club:
            club_data = json.loads(cached_club)
            sub_expire = None
            if club_data.get("sub_expire"):
                sub_expire = datetime.fromisoformat(club_data["sub_expire"])

            club_obj = Club(
                id=club_data["id"],
                name=club_data["name"],
                bot_token=bot_token,
                owner_id=club_data["owner_id"],
                club_settings=club_data["settings"],
                subscription_expire_at=sub_expire
            )
        else:
            # 2. Идем в БД
            session = data["session"]

            result = await session.execute(select(Club).where(Club.bot_token == bot_token))
            club_obj = result.scalar_one_or_none()

            if not club_obj:
                return  # Если клуба нет в базе — прерываем

            # ФИКС: Код сохранения в Redis теперь выполнится (убран из-под if)
            club_to_cache = {
                "id": club_obj.id,
                "name": club_obj.name,
                "bot_token": bot_token,
                "owner_id": club_obj.owner_id,
                "settings": club_obj.club_settings,
                "sub_expire": club_obj.subscription_expire_at.isoformat() if club_obj.subscription_expire_at else None
            }
            await self.redis.set(cache_key, json.dumps(club_to_cache), ex=600)

        # Срок подписки не берём из Redis: он должен блокироваться сразу
        # после истечения и разблокироваться сразу после платежа.
        session = data["session"]
        subscription_result = await session.execute(
            select(Club.subscription_expire_at, Club.owner_id, Club.id)
            .where(Club.bot_token == bot_token)
        )
        subscription_row = subscription_result.one_or_none()
        if not subscription_row:
            return
        club_obj.subscription_expire_at = subscription_row[0]
        club_obj.owner_id = subscription_row[1]
        club_obj.id = subscription_row[2]

        # 3. Логика проверки подписки
        now = datetime.now()
        sub_end = club_obj.subscription_expire_at
        is_owner = (user_id == club_obj.owner_id)
        is_super = (user_id in SUPER_ADMIN_IDS)

        # Флаг оплаты: активна ли подписка прямо сейчас
        is_sub_active = sub_end is not None and sub_end > now

        if not is_super:
            if not is_sub_active:
                # Если это не владелец - жестко отсекаем
                if not is_owner:
                    if isinstance(event, types.Message):
                        await event.answer("❌ Доступ к боту приостановлен.")
                    elif isinstance(event, types.CallbackQuery):
                        await event.answer("❌ Доступ приостановлен.", show_alert=True)
                    return

                # Если это владелец - проверяем, не пытается ли он оплатить прямо сейчас
                is_pay_action = False
                if hasattr(event, "data") and event.data:
                    if event.data == "pay_menu" or event.data.startswith("buy_sub"):
                        is_pay_action = True
                # Успешный платёж обязан пройти до обработчика зачисления,
                # иначе истёкший клуб никогда не продлится.
                if isinstance(event, types.Message) and event.successful_payment:
                    is_pay_action = True

                # Если владелец просто пишет что-то другое или жмет другие кнопки — требуем оплату
                if not is_pay_action:
                    kb = InlineKeyboardBuilder()
                    kb.row(types.InlineKeyboardButton(text="💳 Оплатить доступ", callback_data="pay_menu"))

                    msg_text = (f"⚠️ <b>Доступ ограничен</b>\n\n"
                                f"Ваша подписка истекла: <code>{sub_end.strftime('%d.%m.%Y') if sub_end else 'нет данных'}</code>\n"
                                f"Функции бота временно отключены.")

                    if isinstance(event, types.Message):
                        await event.answer(msg_text, reply_markup=kb.as_markup(), parse_mode="HTML")
                    elif isinstance(event, types.CallbackQuery):
                        await event.message.edit_text(msg_text, reply_markup=kb.as_markup(), parse_mode="HTML")
                    return

        # 4. Прокидываем данные в хендлеры
        data["club"] = club_obj
        data["club_id"] = club_obj.id
        data["is_owner"] = is_owner
        data["is_super_admin"] = is_super
        data["redis"] = self.redis
        data["club_settings"] = club_obj.club_settings
        return await handler(event, data)
