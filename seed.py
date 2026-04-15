import asyncio
import os
from datetime import datetime, timedelta
from database.db import AsyncSessionLocal, Club # Проверь, что async_session_maker так называется


async def seed_first_club():
    # Используем фабрику сессий, которая у тебя в db.py
    async with AsyncSessionLocal() as session:
        admin_club = Club(
            name="Главная панель",
            bot_token=os.getenv("BOT_TOKEN"),
            # ЗАМЕНИЛИ is_active на новую колонку и дали подписку на 100 лет
            subscription_expire_at=datetime.now() + timedelta(days=36500),
            club_settings={},
            owner_id=1271717628
        )
        session.add(admin_club)
        await session.commit()
        print("✅ Мастер-бот добавлен! Подписка активна до 2126 года.")

if __name__ == "__main__":
    asyncio.run(seed_first_club())
