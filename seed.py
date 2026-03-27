import asyncio
import os

from database.db import AsyncSessionLocal
from database.db import Club # проверь путь к импорту


async def seed_first_club():
    async with AsyncSessionLocal() as session:
        # Добавляем твой основной токен как первый клуб
        admin_club = Club(
            name="Главная панель",
            bot_token=os.getenv("BOT_TOKEN"),
            is_active=True,
            club_settings={},
            owner_id=1271717628
        )
        session.add(admin_club)
        await session.commit()
        print("✅ Мастер-бот добавлен в базу! Теперь запускай main.py")

if __name__ == "__main__":
    asyncio.run(seed_first_club())
