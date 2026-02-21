import os
import sys

from aiogram.types import FSInputFile
from loguru import logger
from middlewares.logging_middleware import LoggingMiddleware
import asyncio
from datetime import datetime
from config import ADMIN_IDS
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from handlers import start, admin, buttons
from database.db import init_db, get_daily_stats, get_expire_students, create_db_backup
from handlers.buttons import get_profile_keyboard
from config import BOT_TOKEN
from aiogram.fsm.storage.memory import MemoryStorage


logger.remove()
logger.add(sys.stderr, level='INFO')
logger.add('logs/bot_log.log', rotation='1 MB', retention='10 days', compression="zip", enqueue=True)


async def send_backup_to_admin(bot: Bot):
    path = await create_db_backup()
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_document(
                admin_id,
                FSInputFile(path),
                caption="📦 Еженедельный бэкап базы данных"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки бэкапа: {e}")

    if os.path.exists(path):
        os.remove(path)


async def check_abon_mailing(bot: Bot):
    students = await get_expire_students()
    logger.info(f"Начинаю рассылку для {len(students)} атлетов")
    for s in students:
        try:
            text = (
                f"⚠️ <b>Внимание!</b>\n\n"
                f"У атлета <b>{s.name}</b> скоро истекает абонемент.\n"
                f"Дата окончания: <code>{s.expire_date.strftime('%d.%m.%Y')}</code>\n\n"
                f"Не забудьте продлить его в меню абонементов! 🥊"
            )
            await bot.send_message(
                chat_id=s.parent_id,
                text=text,
                parse_mode="HTML",
                reply_markup=get_profile_keyboard()
            )

            logger.info(f"✅ Уведомление отправлено родителю {s.parent_id} за атлета {s.name}")
            await asyncio.sleep(0.33)

        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления для студента {s.id}: {e}")


async def send_daily_report_to_admins(bot: Bot):
    try:
        visits, active = await get_daily_stats()
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
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(LoggingMiddleware())
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_backup_to_admin, 'cron', day_of_week='sat', hour=19, minute=0, args=(bot,))
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
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("⭕ Бот остановлен пользователем")





# в отчет выгружать купленные абонементы обязательно!
#замок и двери есть инфа в скринах

# добавить логирования к последним блокам оплата налом и регестрация

#добавить трансляицю всем у кого есть абонемент но не было зафиксированно посещение и отправлять уведомление
# упаковка под саас , бэкапы!! docker exec my_postgres pg_dump -U postgres postgres > backup_$(date +%Y-%m-%d).sql
# Поскольку база в Docker, бэкап делается одной командой в терминале (можно засунуть в планировщик на сервере):

#Reddis для отработки флуда
#добавить выручку за день и месяц , сделать уведомление тем кто не посещает ,
# добавить колонку через алимбик с днем рождения и поздравлять, др обязательно