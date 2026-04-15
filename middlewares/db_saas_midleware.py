import json
from datetime import datetime

from aiogram import BaseMiddleware, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger
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
            # ВАЖНО: Восстанавливаем дату из строки обратно в datetime
            sub_expire = None
            if club_data.get("sub_expire"):
                sub_expire = datetime.fromisoformat(club_data["sub_expire"])

            club_obj = Club(
                id=club_data["id"],
                name=club_data["name"],
                owner_id=club_data["owner_id"],
                club_settings=club_data["settings"],
                subscription_expire_at=sub_expire  # Добавили поле
            )
        else:
            # 2. Идем в БД
            session = data["session"]
            result = await session.execute(select(Club).where(Club.bot_token == bot_token))
            club_obj = result.scalar_one_or_none()

            if not club_obj:
                return

                # Сохраняем в Redis (дату превращаем в ISO строку)
            club_to_cache = {
                "id": club_obj.id,
                "name": club_obj.name,
                "owner_id": club_obj.owner_id,
                "settings": club_obj.club_settings,
                "sub_expire": club_obj.subscription_expire_at.isoformat() if club_obj.subscription_expire_at else None
            }
            await self.redis.set(cache_key, json.dumps(club_to_cache), ex=600)

        # 3. Логика проверки подписки
        now = datetime.now()
        sub_end = club_obj.subscription_expire_at
        is_owner = (user_id == club_obj.owner_id)
        is_super = (user_id in SUPER_ADMIN_IDS)

        # Флаг оплаты: активна ли подписка прямо сейчас
        is_sub_active = sub_end is not None and sub_end > now

        if not is_super:
            if not is_sub_active:
                # Если это не владелец - отсекаем
                if not is_owner:
                    if isinstance(event, types.Message):
                        await event.answer("❌ Доступ к боту приостановлен.")
                    return

                # Если это владелец - проверяем, не пытается ли он оплатить прямо сейчас
                is_pay_action = False
                if hasattr(event, "data") and (event.data == "pay_menu" or event.data.startswith("buy_sub")):
                    is_pay_action = True

                # Если владелец просто пишет что-то другое - требуем оплату
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
        data["is_owner"] = is_owner
        data["is_super_admin"] = is_super
        data["redis"] = self.redis

        return await handler(event, data)
