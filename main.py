import sys
from loguru import logger
from middlewares.logging_middleware import LoggingMiddleware
import asyncio
from datetime import datetime
from config import ADMIN_IDS
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from handlers import start, admin, buttons
from database.db import init_db, get_expire_users, get_daily_stats
from handlers.buttons import get_profile_keyboard
from config import BOT_TOKEN


logger.remove()
logger.add(sys.stderr, level='INFO')
logger.add('logs/bot_log.log', rotation='1 MB', retention='10 days', compression="zip", enqueue=True)


async def check_abon_mailing(bot: Bot):
    users = get_expire_users()
    logger.info(f"Начинаю рассылку для {len(users)} пользователей")
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
            logger.info(f"Сообщение успешно отправлено: ID {user_id}")
            await asyncio.sleep(0.33)
        except Exception as e:
            logger.error(f"Ошибка при отправке пользователю {user}: {e}")


async def send_daily_report_to_admins(bot: Bot):
    try:
        visits, active = get_daily_stats()
        logger.info(f"Сбор статистики завершен:{visits} визитов, {active} активных")
    except Exception as e:
        logger.critical(f"Критическая ошибка при получении статистики из БД: {e}")
        return
    report_text = (
         f"🌙 <b>ВЕЧЕРНИЙ ОТЧЕТ</b> ({datetime.now().strftime('%d.%m.%Y')})\n\n"
         f"👤 <b>Посещений за день:</b> <code>{visits}</code>\n"
         f"💎 <b>Активных абонементов:</b> <code>{active}</code>\n"
)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report_text, parse_mode="HTML")
            logger.info(f"Отчет успешно отправлен админу {admin_id}")
        except Exception as e:
            logger.error(f"Не удалось отправить отчет админу {admin_id}: {e}")


async def main():
    logger.info("Инициализация базы данных...")
    init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.message.outer_middleware(LoggingMiddleware())
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_abon_mailing, 'cron', hour=10, minute=0, args=(bot,))
    scheduler.add_job(send_daily_report_to_admins, 'cron', hour=22, minute=0, args=(bot,))
    scheduler.start()
    dp.include_router(start.router)
    dp.include_router(buttons.router)
    dp.include_router(admin.router)
    logger.success("🚀 Бот успешно запущен и готов к работе!")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
if __name__ == '__main__':
    try:
        # Запускаем асинхронную функцию main
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("⭕ Бот остановлен пользователем")




# СРОЧНО ДОБАВИТЬ ДЛЯ АДМИНА ВОЗМОЖНОСТЬ ОФОРМЛЯТЬ АБОНЕМЕНТЫ ВРУЧНУЮ И ОТМЕЧАТЬ ПОСЕЩЕНИЯ !
# остановился на валидации оплаты , так как начал менять логику бд для оплаты без р\с

# в отчет выгружать купленные абонементы обязательно!
