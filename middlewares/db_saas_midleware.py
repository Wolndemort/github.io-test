import json
from aiogram import BaseMiddleware
from sqlalchemy import select
from database.db import Club
from redis.asyncio import Redis


class ClubMiddleware(BaseMiddleware):
    def __init__(self, redis: Redis):
        self.redis = redis
        super().__init__()

    async def __call__(self, handler, event, data):
        if not getattr(event, "from_user", None):
            return await handler(event, data)

        bot_token = data["bot"].token
        cache_key = f"club_config:{bot_token}"

        # 1. Пытаемся взять из Redis
        cached_club = await self.redis.get(cache_key)

        if cached_club:
            club_data = json.loads(cached_club)
            data["club_id"] = club_data["id"]
            data["club_settings"] = club_data["settings"]
            data["club_name"] = club_data["name"]
            # owner_id тоже в кэш, чтобы is_owner работал
            data["is_owner"] = (event.from_user.id == club_data["owner_id"])
        else:
            # 2. Если в кэше нет — идем в БД
            session = data["session"]
            result = await session.execute(select(Club).where(Club.bot_token == bot_token))
            club = result.scalar_one_or_none()

            if not club: return  # Игнорим левые боты

            # Сохраняем в Redis на 10 минут (600 сек)
            club_to_cache = {
                "id": club.id,
                "name": club.name,
                "owner_id": club.owner_id,
                "settings": club.club_settings
            }
            await self.redis.set(cache_key, json.dumps(club_to_cache), ex=600)

            data["club_id"] = club.id
            data["club_settings"] = club.club_settings
            data["is_owner"] = (event.from_user.id == club.owner_id)
        return await handler(event, data)
