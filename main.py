import logging
import asyncio
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from handlers import start, admin, buttons
from database.db import init_db, get_expire_users
from handlers.buttons import get_profile_keyboard
from config import BOT_TOKEN


async def check_abon_mailing(bot: Bot):
    users = get_expire_users()
    for user_data in users:
        try:
            user_id = user_data[0]
            user_name = user_data[1] if user_data[1] else "Атлет"
            await bot.send_message(
                user_id,
                f'⚠️ Внимание {user_name}! Ваш абонемент скоро истекает. Не забудьте продлить его! 🥊',
                parse_mode="HTML",
                reply_markup=get_profile_keyboard()
            )
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"Ошибка отправки пользователю {user_data}: {e}")


async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    bot = Bot(token=BOT_TOKEN)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_abon_mailing, 'cron', hour=10, minute=0, args=(bot,))
    scheduler.start()
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(buttons.router)
    dp.include_router(admin.router)
    print("🚀 Бот успешно запущен и готов к работе!")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
if __name__ == '__main__':
    try:
        # Запускаем асинхронную функцию main
        asyncio.run(main())
    except KeyboardInterrupt:
        print("⭕ Бот остановлен пользователем")


#вернулся в мастерс после кьюар кода , нужно упаковатьв докер , исправить отправку рассылки и выгрузки файл ,