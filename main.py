import logging
import asyncio
from datetime import datetime
from config import ADMIN_IDS
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from handlers import start, admin, buttons
from database.db import init_db, get_expire_users, get_daily_stats
from handlers.buttons import get_profile_keyboard
from config import BOT_TOKEN


async def check_abon_mailing(bot: Bot):
    users = get_expire_users()
    for user in users:
        try:
            user_id = user.user_id
            user_name = user.full_name if user.full_name else "Атлет"
            await bot.send_message(
                user_id,
                f'⚠️ Внимание {user_name}! Ваш абонемент скоро истекает. Не забудьте продлить его! 🥊',
                parse_mode="HTML",
                reply_markup=get_profile_keyboard()
            )
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"Ошибка отправки пользователю {user}: {e}")


async def send_daily_report_to_admins(bot: Bot):
    visits, active = get_daily_stats()
    report_text = (
         f"🌙 <b>ВЕЧЕРНИЙ ОТЧЕТ</b> ({datetime.now().strftime('%d.%m.%Y')})\n\n"
         f"👤 <b>Посещений за день:</b> <code>{visits}</code>\n"
         f"💎 <b>Активных абонементов:</b> <code>{active}</code>\n"
)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report_text, parse_mode="HTML")
        except Exception:
            pass


async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    bot = Bot(token=BOT_TOKEN)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_abon_mailing, 'cron', hour=10, minute=0, args=(bot,))
    scheduler.add_job(send_daily_report_to_admins, 'cron', hour=22, minute=0, args=(bot,))
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


