import asyncio
import os
import sys
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from loguru import logger
from sqlalchemy import select
from admin_module.sqladmin import setup_admin
from config import ADMIN_IDS
from database.db import init_db, get_daily_stats, get_expire_students_grouped, create_db_backup, engine, \
    AsyncSessionLocal, Club
from handlers import start, user_option, buttons, payments, admin_option, super_admin_handlers
from handlers.buttons import get_profile_keyboard
from middlewares.db_saas_midleware import ClubMiddleware
from middlewares.main_middleware import DbSessionMiddleware
from admin_module.api import router
from redis.asyncio import Redis
from aiogram.fsm.storage.redis import RedisStorage

redis_client = Redis(host='redis', port=6379, db=0)
logger.remove()
logger.add(sys.stderr, level='INFO')
logger.add('logs/bot_log.log', rotation='1 MB', retention='10 days', compression="zip", enqueue=True)
app = FastAPI()
app.include_router(router)
setup_admin(app, engine)
storage = RedisStorage(redis=redis_client)


async def send_backup_to_admin(bots_dict: dict):
    """
    Создает бэкап всей БД и отправляет Супер-админам (тебе).
    Использует первого доступного бота из системы.
    """
    # 1. Создаем файл бэкапа
    path = await create_db_backup()
    if not path or not os.path.exists(path):
        logger.error("❌ Файл бэкапа не был создан!")
        return

    # 2. Берем любого живого бота из словаря, чтобы отправить файл
    # (Бэкап всей базы SaaS летит только создателю платформы)
    random_bot = next(iter(bots_dict.values()), None)

    if not random_bot:
        logger.error("❌ Нет активных ботов для отправки бэкапа!")
        return

    for admin_id in ADMIN_IDS:
        try:
            await random_bot.send_document(
                admin_id,
                document=types.FSInputFile(path),
                caption=f"📦 **SaaS Full Backup**\n📅 Дата: `{datetime.now().strftime('%d.%m.%Y')}`"
            )
            logger.info(f"✅ Бэкап отправлен супер-админу {admin_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки бэкапа админу {admin_id}: {e}")

    # 3. Чистим за собой
    if os.path.exists(path):
        os.remove(path)
        logger.debug("🗑️ Временный файл бэкапа удален")


async def check_abon_mailing(bots_dict: dict):
    """
    Рассылка уведомлений об истекающих абонементах.
    """
    # 1. Получаем сессию, чтобы достать настройки клубов
    async with AsyncSessionLocal() as session:
        # Твой метод возвращает (Student, bot_token)
        data = await get_expire_students_grouped(session)

        logger.info(f"🚀 SaaS Рассылка: Найдено {len(data)} атлетов")

        for student, token in data:
            try:
                current_bot = bots_dict.get(token)
                if not current_bot:
                    continue

                # 2. КРИТИЧНЫЙ МОМЕНТ: Нам нужны настройки ЭТОГО клуба для клавиатуры
                # Быстрый запрос настроек (позже вынеси это в JOIN в функции get_expire_students_grouped)
                result = await session.execute(
                    select(Club.club_settings).where(Club.bot_token == token)
                )
                club_settings = result.scalar() or {}

                text = (
                    f"⚠️ <b>Внимание!</b>\n\n"
                    f"У атлета <b>{student.name}</b> скоро истекает абонемент.\n"
                    f"Дата окончания: <code>{student.expire_date.strftime('%d.%m.%Y')}</code>\n\n"
                    f"Не забудьте продлить его в меню! 🥊"
                )

                # 3. Теперь передаем club_settings в клавиатуру
                await current_bot.send_message(
                    chat_id=student.parent_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=get_profile_keyboard(club_settings)
                )

                logger.info(f"✅ [Клуб {student.club_id}] Отправлено родителю {student.parent_id}")
                await asyncio.sleep(0.05)

            except Exception as e:
                logger.error(f"❌ Ошибка отправки (Student ID {student.id}): {e}")


async def send_daily_report_to_admins(bots_dict: dict):
    """
    Рассылка вечерних отчетов владельцам каждого клуба.
    """
    from database.db import AsyncSessionLocal, Club, select  # Импорт внутри, чтобы избежать циклов

    async with AsyncSessionLocal() as session:
        # 1. Берем все активные клубы
        result = await session.execute(select(Club).where(Club.is_active == True))
        clubs = result.scalars().all()

    for club in clubs:
        try:
            # 2. Берем бота из словаря по токену
            bot = bots_dict.get(club.bot_token)
            if not bot:
                continue

            # 3. Считаем статистику именно для ЭТОГО клуба
            async with AsyncSessionLocal() as session:
                visits, active = await get_daily_stats(club_id=club.id, session=session)

            report_text = (
                f"🌙 <b>ВЕЧЕРНИЙ ОТЧЕТ: {club.name}</b>\n"
                f"📅 Дата: <code>{datetime.now().strftime('%d.%m.%Y')}</code>\n\n"
                f"👤 <b>Посещений сегодня:</b> <code>{visits}</code>\n"
                f"💎 <b>Активных абонементов:</b> <code>{active}</code>\n"
            )

            # 4. Отправляем владельцу клуба (owner_id)
            if club.owner_id:
                await bot.send_message(club.owner_id, report_text, parse_mode="HTML")
                logger.info(f"✅ Отчет клуба {club.id} отправлен владельцу {club.owner_id}")

        except Exception as e:
            logger.error(f"❌ Ошибка отчета для клуба {club.id}: {e}")


async def main():
    logger.info("🚀 Инициализация системы SaaS...")
    await init_db()

    # 1. Загружаем все активные клубы из БД
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Club).where(Club.is_active == True))
        active_clubs = result.scalars().all()

    # 2. Создаем маппинг ботов {token: bot_instance}
    bots_dict = {}
    for club in active_clubs:
        try:
            # Пытаемся создать экземпляр бота
            bot = Bot(
                token=club.bot_token,
                default=DefaultBotProperties(parse_mode="HTML")
            )

            # Проверка: живой ли токен (делаем легкий запрос к API)
            # Это гарантирует, что бот не упадет позже при старте поллинга
            await bot.get_me()

            bots_dict[club.bot_token] = bot
            logger.info(f"✅ Бот для клуба '{club.name}' (ID: {club.id}) готов к работе.")

        except Exception as e:
            logger.error(f"❌ Ошибка токена для клуба '{club.name}' (ID: {club.id}): {e}")
            # Просто идем дальше, не давая ошибке одного бота уронить всю систему
            continue

    if not bots_dict:
        logger.critical("❌ Нет ни одного валидного активного токена в БД! Работа невозможна.")
        return

    # 3. Настройка диспетчера
    dp = Dispatcher()

    # Мидлвари (DbSessionMiddleware должен быть Outer, ClubMiddleware - Inner)
    dp.message.outer_middleware(DbSessionMiddleware(session_pool=AsyncSessionLocal))
    dp.message.outer_middleware(ClubMiddleware(redis=redis_client))
    dp.callback_query.outer_middleware(ClubMiddleware(redis=redis_client))

    # Роутеры (Порядок важен!)
    # Сначала проверяем права доступа (Admin), потом общие команды
    dp.include_router(super_admin_handlers.router)
    dp.include_router(start.router)
    dp.include_router(admin_option.router)  # Админку лучше повыше
    dp.include_router(payments.router)
    dp.include_router(user_option.router)
    dp.include_router(buttons.router)

    # 4. Настройка планировщика
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_abon_mailing, 'cron', hour=10, minute=0, args=(bots_dict,))
    scheduler.add_job(send_daily_report_to_admins, 'cron', hour=22, minute=0, args=(bots_dict,))
    scheduler.start()

    logger.success(f"✅ SaaS запущен! Работает ботов: {len(bots_dict)} из {len(active_clubs)}")

    try:
        # Запускаем поллинг для всех ботов одновременно
        await dp.start_polling(*bots_dict.values())
    finally:
        # Корректное закрытие сессий всех ботов
        for bot in bots_dict.values():
            await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")


# замок и двери есть инфа в скринах

# добавить логирования к последним блокам оплата налом и регестрация

# добавить трансляицю всем у кого есть абонемент но не было зафиксированно посещение и отправлять уведомление
# упаковка под саас

# добавить выручку за день и месяц , сделать уведомление тем кто не посещает ,
# добавить колонку через алимбик с днем рождения и поздравлять, др обязательно
# сделал докеригноре , осталось оплатить тайм веб и выложить через гит по идее добавить магин айди в админы
# указывать именно имя и фамилию!!! в регестр
# .env.example , прокинуть сессию через middlewear  paytest, sentry
# Logging (Structlog): Вместо обычных принтов внедри структурированное логирование в JSON.
# Это позволит в будущем легко анализировать логи через ELK-стек или Grafana Loki.
# CI/CD (GitHub Actions): Настрой автоматический запуск твоих новых Pytest при каждом git push.
# Если тесты упали — деплой блокируется. Это сэкономит кучу нервов.
# Prometheus + Grafana: Есл
# Уведомление тем кого не было 10 дней
# добавил овнер айди для админ панели прокинул в мидлвер, я супер админ, добавил индексы,
# добавил колонку ситинг и клуб, перебрал майн, старт,
