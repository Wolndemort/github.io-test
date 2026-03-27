import json
from aiogram import BaseMiddleware
from sqlalchemy import select
from database.db import Club
from redis.asyncio import Redis

# Список ID супер-админов (добавь свой ID сюда)
SUPER_ADMIN_IDS = [1271717628]


class ClubMiddleware(BaseMiddleware):
    def __init__(self, redis: Redis):
        self.redis = redis
        super().__init__()

    async def __call__(self, handler, event, data):
        # Игнорируем события без пользователя (например, системные)
        if not getattr(event, "from_user", None):
            return await handler(event, data)

        user_id = event.from_user.id
        bot_token = data["bot"].token
        cache_key = f"club_config:{bot_token}"

        # 1. Пытаемся взять данные из Redis
        cached_club = await self.redis.get(cache_key)

        club_obj = None

        if cached_club:
            club_data = json.loads(cached_club)
            # Создаем объект Club из кэша, чтобы хендлеры видели его как модель
            club_obj = Club(
                id=club_data["id"],
                name=club_data["name"],
                owner_id=club_data["owner_id"],
                club_settings=club_data["settings"]
            )
        else:
            # 2. Если в кэше нет — идем в БД
            session = data["session"]
            result = await session.execute(select(Club).where(Club.bot_token == bot_token))
            club_obj = result.scalar_one_or_none()

            if not club_obj:
                return  # Бот не зарегистрирован в нашей системе

            # Сохраняем в Redis на 10 минут
            club_to_cache = {
                "id": club_obj.id,
                "name": club_obj.name,
                "owner_id": club_obj.owner_id,
                "settings": club_obj.club_settings
            }
            await self.redis.set(cache_key, json.dumps(club_to_cache), ex=600)

        # 3. Наполняем data всеми нужными аргументами для хендлеров
        data["club"] = club_obj
        data["club_id"] = club_obj.id
        data["club_settings"] = club_obj.club_settings
        data["is_owner"] = (user_id == club_obj.owner_id)

        # Добавляем ключ, который требовал хендлер в логах (is_super_adm)
        data["is_super_adm"] = (user_id in SUPER_ADMIN_IDS)

        return await handler(event, data)
