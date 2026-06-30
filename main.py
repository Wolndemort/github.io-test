import asyncio
import os
import sys
from contextlib import asynccontextmanager
from fastapi import Request
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
    AsyncSessionLocal, Club, Student
from handlers import start, user_option, buttons, payments, admin_option, super_admin_handlers
from handlers.buttons import get_profile_keyboard
from middlewares.db_saas_midleware import ClubMiddleware
from middlewares.main_middleware import DbSessionMiddleware
from admin_module.api import router
from redis.asyncio import Redis
from aiogram.fsm.storage.redis import RedisStorage

logger.remove()
logger.add(sys.stderr, level='INFO')
logger.add('logs/bot_log.log', rotation='1 MB', retention='10 days', compression="zip", enqueue=True)

# Инициализация Redis и FSM
redis_client = Redis(host='redis', port=6379, db=0)
storage = RedisStorage(redis=redis_client)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- ЭТО БЫВШИЙ STARTUP ---
    logger.info("🚀 Инициализация системы SaaS Webhooks...")
    await init_db()

    # 1. Загружаем все активные клубы из БД
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Club).where(Club.bot_token.is_not(None))
        )
        active_clubs = result.scalars().all()

    # 2. Создаем экземпляры ботов
    for club in active_clubs:
        try:
            bot = Bot(
                token=club.bot_token,
                default=DefaultBotProperties(parse_mode="HTML")
            )
            bots_dict[club.bot_token] = bot

            # Регистрируем вебхук в Telegram
            webhook_url = f"{BASE_URL}/webhook/bot/{club.bot_token}"
            await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
            logger.info(f"✅ ВЕБХУК запущен: Клуб '{club.name}' -> {webhook_url[:35]}...")
        except Exception as e:
            logger.error(f"❌ Ошибка токена для клуба '{club.name}': {e}")

    # 3. Настройка диспетчера aiogram
    dp.message.outer_middleware(DbSessionMiddleware(session_pool=AsyncSessionLocal))
    dp.callback_query.outer_middleware(DbSessionMiddleware(session_pool=AsyncSessionLocal))
    dp.message.outer_middleware(ClubMiddleware(redis=redis_client))
    dp.callback_query.outer_middleware(ClubMiddleware(redis=redis_client))

    # Подключаем роутеры
    dp.include_router(super_admin_handlers.router)
    dp.include_router(start.router)
    dp.include_router(admin_option.router)
    dp.include_router(payments.router)
    dp.include_router(user_option.router)
    dp.include_router(buttons.router)

    # 4. Фоновые SaaS-задачи (APScheduler)
    scheduler = AsyncIOScheduler()

    # Утренний блок (10:00) — Проверка ДР, прогульщиков и рассылка по абонементам
    scheduler.add_job(saas_daily_morning_check, 'cron', hour=10, minute=0)
    scheduler.add_job(check_abon_mailing, 'cron', hour=10, minute=0)

    # Вечерний блок (22:00) — Отчет по посещениям и абонементам для владельцев клубов
    scheduler.add_job(send_daily_report_to_admins, 'cron', hour=22, minute=0)

    # Ночной блок (23:00) — Полный бэкап всей базы данных тебе в личку
    scheduler.add_job(send_backup_to_admin, 'cron', hour=23, minute=0)

    scheduler.start()
    logger.success(f"🔥 Все фоновые SaaS-задачи (ДР, Масс-майлинг, Отчеты, Бэкапы) успешно запущены!")

    yield # <--- МАГИЧЕСКАЯ СТРОКА: Здесь FastAPI запускается и ждет запросы

    # --- ЭТО БЫВШИЙ SHUTDOWN (сработает при выключении сервера) ---
    logger.info("🛑 Закрытие сессий ботов...")
    for bot in bots_dict.values():
        await bot.session.close()

# Инициализация FastAPI
app = FastAPI(title="SpeedyCRM SaaS API", lifespan=lifespan)
app.include_router(router)
setup_admin(app, engine)

# Глобальный маппинг ботов
bots_dict = {}
dp = Dispatcher(storage=storage)

# Настройка путей вебхуков
BASE_URL = "https://speedycrm.ru"


async def send_backup_to_admin():
    """
    Создает бэкап всей БД и отправляет Супер-админам (тебе).
    Использует глобальный словарь bots_dict.
    """
    # 1. Создаем файл бэкапа (вызывает твою готовую функцию)
    path = await create_db_backup()
    if not path or not os.path.exists(path):
        logger.error("❌ Файл бэкапа не был создан!")
        return

    # 2. Берем любого живого бота из глобального словаря
    random_bot = next(iter(bots_dict.values()), None)

    if not random_bot:
        logger.error("❌ Нет активных ботов для отправки бэкапа!")
        return

    # Пробегаемся по списку твоих ADMIN_IDS
    for admin_id in ADMIN_IDS:
        try:
            await random_bot.send_document(
                chat_id=admin_id,
                document=types.FSInputFile(path),
                caption=f"📦 <b>SaaS Full Backup</b>\n📅 Дата: <code>{datetime.now().strftime('%d.%m.%Y')}</code>"
            )
            logger.info(f"✅ Бэкап отправлен супер-админу {admin_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки бэкапа админу {admin_id}: {e}")

    # 3. Чистим за собой временный файл на сервере Aezza
    if os.path.exists(path):
        os.remove(path)
        logger.debug("🗑️ Временный файл бэкапа удален с диска")


async def check_abon_mailing():
    """
    Рассылка уведомлений об истекающих абонементах.
    Использует глобальный словарь bots_dict.
    """
    async with AsyncSessionLocal() as session:
        data = await get_expire_students_grouped(session)

        logger.info(f"🚀 SaaS Рассылка: Найдено {len(data)} атлетов с истекающими абонементами.")

        for student, token in data:
            try:
                # Берем бота напрямую из глобального маппинга
                current_bot = bots_dict.get(token)
                if not current_bot:
                    continue

                # Достаем настройки конкретного клуба для красивой клавиатуры
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

                await current_bot.send_message(
                    chat_id=student.parent_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=get_profile_keyboard(club_settings)
                )

                logger.info(f"✅ [Клуб {student.club_id}] Отправлено родителю {student.parent_id}")
                await asyncio.sleep(0.05) # Защита от лимитов Telegram API

            except Exception as e:
                logger.error(f"❌ Ошибка отправки (Student ID {student.id}): {e}")


async def send_daily_report_to_admins():
    """
    Рассылка вечерних отчетов владельцам каждого клуба.
    Использует глобальный словарь bots_dict.
    """
    from database.db import AsyncSessionLocal, Club, select  # Защита от циклических импортов

    async with AsyncSessionLocal() as session:
        # 1. Берем все активные клубы
        result = await session.execute(select(Club).where(Club.subscription_expire_at >= datetime.now()))
        clubs = result.scalars().all()

    for club in clubs:
        try:
            # 2. Берем бота напрямую из глобального маппинга по токену
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


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 Закрытие сессий ботов...")
    for bot in bots_dict.values():
        await bot.session.close()


# 5. Главный Эндпоинт, куда Nginx будет присылать вебхуки от Telegram
@app.post("/webhook/bot/{token}")
async def handle_telegram_webhook(token: str, request: Request):
    if token not in bots_dict:
        logger.warning(f"⚠️ Попытка вызова вебхука неизвестным токеном: {token[:15]}...")
        return {"status": "unauthorized"}

    bot = bots_dict[token]
    update_data = await request.json()
    update = types.Update(**update_data)

    # Прокидываем апдейт напрямую в диспетчер aiogram
    await dp.feed_update(bot, update)
    return {"status": "ok"}


# --- НОВЫЕ БИЗНЕС-ФИЧИ ИЗ ТВОЕГО ТЗ ---

async def saas_daily_morning_check():
    """Ежедневная фоновая проверка: Дни рождения и Прогульщики (10 дней без посещений)"""
    logger.info("📊 Запуск фонового анализа базы атлетов...")
    async with AsyncSessionLocal() as session:
        # Ищем всех студентов
        result = await session.execute(select(Student))
        students = result.scalars().all()

        now = datetime.now()

        for student in students:
            # Вытаскиваем токен клуба этого студента для отправки сообщения
            club_result = await session.execute(select(Club).where(Club.id == student.club_id))
            club = club_result.scalar_one_or_none()
            if not club or club.bot_token not in bots_dict:
                continue
            bot = bots_dict[club.bot_token]

            # 🎂 Фича 1: Поздравление с Днем Рождения (если колонка добавления через Alembic активна)
            if hasattr(student, 'birthday') and student.birthday:
                if student.birthday.month == now.month and student.birthday.day == now.day:
                    try:
                        await bot.send_message(
                            chat_id=student.parent_id,
                            text=f"🎂 Клуб <b>{club.name}</b> поздравляет атлета <b>{student.name}</b> с Днём Рождения! 🎉\nЖелаем новых спортивных побед и крепкого здоровья!"
                        )
                        logger.info(f"🎉 Поздравление с ДР отправлено атлету {student.name}")
                    except Exception as e:
                        logger.error(f"Не удалось отправить ДР сообщение родителю {student.parent_id}: {e}")

            # 🏃‍♂️ Фича 2: Контроль прогульщиков (Не было 10 дней, но есть активный абонемент)
            if student.last_visit and student.balance_lessons > 0 and not student.is_frozen:
                days_absent = (now - student.last_visit).days
                if days_absent == 10:
                    try:
                        await bot.send_message(
                            chat_id=student.parent_id,
                            text=f"👋 Здравствуйте! Мы заметили, что атлет <b>{student.name}</b> не посещал тренировки уже 10 дней. Мы соскучились! Ждём вас на занятиях. 😉"
                        )
                        logger.info(f"📢 Уведомление о прогуле (10 дней) отправлено атлету {student.name}")
                    except Exception as e:
                        logger.error(f"Не удалось отправить уведомление прогульщику: {e}")

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
