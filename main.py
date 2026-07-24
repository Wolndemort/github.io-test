import asyncio
from html import escape
import os
import uuid
import time as time_module

from starlette.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from services.analytics import calculate_daily_business_report, calculate_admin_dashboard
from datetime import timedelta, time, timezone
from zoneinfo import ZoneInfo
import logging as logging
from database.db import Subscription, PaymentOrder, User
import sys
try:
    import sentry_sdk
except ImportError:  # optional locally; requirements installs it in production
    sentry_sdk = None
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from contextlib import asynccontextmanager
from fastapi import Request
from fastapi.responses import JSONResponse
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
from handlers import start, user_option, buttons, payments, admin_option, super_admin_handlers,official_payment,\
    super_admin_payment
from handlers.buttons import get_profile_keyboard
from middlewares.db_saas_midleware import ClubMiddleware
from middlewares.main_middleware import DbSessionMiddleware
from admin_module.api import router
from redis.asyncio import Redis
from aiogram.fsm.storage.redis import RedisStorage

logger.remove()
logger.add(sys.stderr, level='INFO')
logger.add('logs/bot_log.log', rotation='1 MB', retention='10 days', compression="zip", enqueue=True)
logger = logging.getLogger("uvicorn.error")


def _scrub_sentry_event(event, hint):
    """Do not send Telegram initData or credentials to Sentry."""
    request = event.get("request")
    if isinstance(request, dict):
        if request.get("url"):
            request["url"] = str(request["url"]).split("?", 1)[0]
        request.pop("query_string", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in list(headers):
                if str(name).lower() in {"authorization", "cookie", "x-api-key"}:
                    headers[name] = "[Filtered]"
    return event


SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN and sentry_sdk is not None:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        release=os.getenv("SENTRY_RELEASE"),
        send_default_pii=False,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
        before_send=_scrub_sentry_event,
    )


# Инициализация Redis и FSM
redis_client = Redis(host='redis', port=6379, db=0)
storage = RedisStorage(redis=redis_client)
BASE_URL = "https://speedycrm.ru"


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
    dp.include_router(official_payment.router)
    dp.include_router(user_option.router)
    dp.include_router(buttons.router)
    dp.include_router(super_admin_payment.router)

    # 4. Фоновые SaaS-задачи (APScheduler)
    scheduler = AsyncIOScheduler(timezone=ZoneInfo("Europe/Moscow"))

    # Утренний блок (10:00) — Проверка ДР, прогульщиков и рассылка по абонементам
    scheduler.add_job(saas_daily_morning_check, 'cron', hour=10, minute=0, id="daily_morning_notifications", replace_existing=True, coalesce=True, max_instances=1, misfire_grace_time=3600)
    # Вторую массовую рассылку запускаем после напоминаний о датах рождения.
    scheduler.add_job(check_abon_mailing, 'cron', hour=10, minute=5, id="expiring_pass_notifications", replace_existing=True, coalesce=True, max_instances=1, misfire_grace_time=3600)

    # Вечерний блок (22:00) — Отчет по посещениям и абонементам для владельцев клубов
    scheduler.add_job(send_daily_report_to_admins, 'cron', hour=22, minute=0, id="daily_admin_report", replace_existing=True, coalesce=True, max_instances=1, misfire_grace_time=3600)
    # Ночной блок (01:00) — Автоматические списания по подпискам ЮKassa
    # Пока оставляем закомментированным, как ты и хотел!
    # Как закончишь тесты в ЛК ЮKassa — просто убери решетку (#) в начале строки.
    #scheduler.add_job(saas_recurrent_payments_job, 'cron', hour=3, minute=0, args=[AsyncSessionLocal])
    scheduler.add_job(auto_close_sessions_job, 'interval', minutes=1, id="auto_close_sessions", replace_existing=True, coalesce=True, max_instances=1, misfire_grace_time=60)
    # Ночной блок (23:00) — Полный бэкап всей базы данных тебе в личку
    scheduler.add_job(send_backup_to_admin, 'cron', hour=23, minute=0, id="daily_database_backup", replace_existing=True, coalesce=True, max_instances=1, misfire_grace_time=3600)
    scheduler.start()
    logger.info(f"🔥 Все фоновые SaaS-задачи (ДР, Масс-майлинг, Отчеты, Бэкапы) успешно запущены!")
    app.state.bots_dict = bots_dict
    yield # <--- МАГИЧЕСКАЯ СТРОКА: Здесь FastAPI запускается и ждет запросы

    # --- ЭТО БЫВШИЙ SHUTDOWN (сработает при выключении сервера) ---
    scheduler.shutdown(wait=False)
    logger.info("Планировщик фоновых задач остановлен")
    logger.info("🛑 Закрытие сессий ботов...")
    for bot in bots_dict.values():
        await bot.session.close()

# Инициализация FastAPI
app = FastAPI(title="SpeedyCRM SaaS API", lifespan=lifespan)
app.state.redis_client = redis_client
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", ""), https_only=os.getenv("COOKIE_SECURE", "1") == "1")
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(router)
setup_admin(app, engine)

# Глобальный маппинг ботов
bots_dict = {}
dp = Dispatcher(storage=storage)


@app.middleware("http")
async def request_monitoring_middleware(request: Request, call_next):
    started = time_module.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.exception(
            "Unhandled error in request path=%s method=%s",
            request.url.path,
            request.method,
        )
        raise
    duration_ms = (time_module.perf_counter() - started) * 1000
    logger.info(
        "HTTP %s %s -> %s (%.1f ms)",
        request.method,
        request.url.path,
        getattr(response, "status_code", 500),
        duration_ms,
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "Unhandled exception path=%s method=%s",
        request.url.path,
        request.method,
    )
    return JSONResponse(
    {
        "status": "error",
        "detail": "Internal server error",
    },
        status_code=500,
    )


@app.get("/health")
async def healthcheck():
    return {
        "status": "ok",
        "service": "SpeedyCRM SaaS API",
        "bots_active": len(bots_dict),
        "time_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
async def readinesscheck():
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(select(Club.id).limit(1))
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {type(exc).__name__}"

    payload = {
        "status": "ok" if db_status == "ok" else "degraded",
        "db": db_status,
        "bots_active": len(bots_dict),
        "redis": "ok" if redis_client else "unknown",
    }
    if db_status != "ok":
        return JSONResponse(payload, status_code=503)
    return payload


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
    Использует глобальный словарь bots_dict и безопасную подгрузку данных родителей.
    """
    async with AsyncSessionLocal() as session:
        # Получаем данные. Убедись, что в твоей функции get_expire_students_grouped
        # модель Student идет в связке с загруженным parent, либо подгрузи ее здесь.
        data = await get_expire_students_grouped(session)

        logger.info(f"🚀 SaaS Рассылка: Найдено {len(data)} атлетов с истекающими абонементами.")

        for student, token in data:
            try:
                # 1. Проверяем наличие бота в глобальном маппинге
                current_bot = bots_dict.get(token)
                if not current_bot:
                    logger.warning(f"⚠️ Бот с токеном ...{token[-8:]} не найден в bots_dict")
                    continue

                # 2. Достаем клуб и его настройки через явный запрос
                # Используем scalar_one_or_none для безопасности
                club_res = await session.execute(
                    select(Club).where(Club.bot_token == token)
                )
                club = club_res.scalar_one_or_none()
                club_settings = club.club_settings if club else {}

                # 3. Нам нужен объект User (родитель) для клавиатуры,
                # чтобы прочитать его user_id и club_id
                user_res = await session.execute(
                    select(User).where(User.user_id == student.parent_id)
                )
                parent_user = user_res.scalar_one_or_none()

                if not parent_user:
                    logger.error(f"❌ Родтель с ID {student.parent_id} не найден в базе данных.")
                    continue

                # Формируем текст уведомления
                expire_str = student.expire_date.strftime('%d.%m.%Y') if student.expire_date else "Не указана"
                text = (
                    f"⚠️ <b>Внимание!</b>\n\n"
                    f"У атлета <b>{student.name}</b> скоро истекает абонемент.\n"
                    f"Дата окончания: <code>{expire_str}</code>\n\n"
                    f"Не забудьте продлить его в меню! 🥊"
                )

                # 4. ФИКС КЛАВИАТУРЫ: Передаем все обязательные параметры
                # Так как это рассылка авторизованному родителю, ставим is_authorized=True
                reply_markup = get_profile_keyboard(
                    user=parent_user,
                    club_settings=club_settings,
                    is_authorized=True
                )

                await current_bot.send_message(
                    chat_id=student.parent_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )

                logger.info(f"✅ [Клуб {student.club_id}] Отправлено родителю {student.parent_id}")
                await asyncio.sleep(0.05) # Защита от лимитов Telegram API (Anti-flood)

            except Exception as e:
                logger.error(f"❌ Ошибка отправки (Student ID {student.id}): {e}")


async def send_daily_report_to_admins():
    """
    Рассылка продвинутых вечерних ИИ-бизнес-отчетов владельцам каждого клуба.
    Сравнивает кассу со вчерашним днем, находит пиковые часы и лучшую дисциплину.
    """
    now = datetime.utcnow()

    # Временные границы для фильтрации SQL
    start_of_today = datetime.combine(now.date(), time.min)
    start_of_yesterday = start_of_today - timedelta(days=1)

    async with AsyncSessionLocal() as session:
        # 1. Загружаем все активные клубы, у которых не кончилась SaaS подписка
        result = await session.execute(select(Club).where(Club.subscription_expire_at >= now))
        clubs = result.scalars().all()

    for club in clubs:
        try:
            bot = bots_dict.get(club.bot_token)
            if not bot or not club.owner_id:
                continue

            async with AsyncSessionLocal() as session:
                # 2. Твой базовый метод подсчета визитов за сегодня
                visits, active_passes = await get_daily_stats(club_id=club.id, session=session)

                # 3. Достаем студентов клуба
                student_res = await session.execute(select(Student).where(Student.club_id == club.id))
                students = list(student_res.scalars().all())

                # 4. Достаем успешные платежи за СЕГОДНЯ
                today_pay_res = await session.execute(
                    select(PaymentOrder).where(
                        PaymentOrder.club_id == club.id,
                        PaymentOrder.status == "CONFIRMED",
                        PaymentOrder.created_at >= start_of_today
                    )
                )
                today_payments = list(today_pay_res.scalars().all())

                # 5. Достаем успешные платежи за ВЧЕРА (между вчерашней полночью и сегодняшней)
                yesterday_pay_res = await session.execute(
                    select(PaymentOrder).where(
                        PaymentOrder.club_id == club.id,
                        PaymentOrder.status == "CONFIRMED",
                        PaymentOrder.created_at >= start_of_yesterday,
                        PaymentOrder.created_at < start_of_today
                    )
                )
                yesterday_payments = list(yesterday_pay_res.scalars().all())

            # Прогоняем данные через наши Pandas-сервисы
            biz_metrics = calculate_daily_business_report(students, today_payments, yesterday_payments)
            admin_metrics = calculate_admin_dashboard(students)

            # Вытаскиваем проблемные зоны для админа
            expired_count = len(admin_metrics.get("expired_students", [])) if not admin_metrics.get("empty") else 0
            sleeping_count = len(admin_metrics.get("sleeping_students", [])) if not admin_metrics.get("empty") else 0

            # Переводим технические ключи дисциплин в человеческие названия из настроек клуба
            config_disciplines = club.club_settings.get("disciplines", {})
            top_disc_key = biz_metrics["top_discipline"].lower()

            # Ищем название дисциплины в конфиге, если не нашли — оставляем как есть
            human_discipline_name = config_disciplines.get(top_disc_key, {}).get("name", biz_metrics["top_discipline"])

            # 🚀 СБОРКА ИИ-ОТЧЕТА ДЛЯ БОССА
            report_text = (
                f"📊 <b>ГЛУБОКИЙ БИЗНЕС-ОТЧЕТ: {club.name}</b>\n"
                f"📅 Дата: <code>{now.strftime('%d.%m.%Y')}</code>\n\n"
                f"💰 <b>Касса сегодня:</b> <code>{biz_metrics['revenue_today']} ₽</code>\n"
                f"⚖️ <b>Динамика ко вчера:</b> <code>{biz_metrics['revenue_diff_text']}</code>\n"
                f"👤 <b>Всего клиентов в базе:</b> <code>{biz_metrics['total_athletes']} чел.</code>\n\n"
                f"📈 <b>ОПЕРАТИВНЫЙ АНАЛИЗ ЗА ДЕНЬ:</b>\n"
                f"🚶‍♂️ Посещений зала: <code>{visits}</code>\n"
                f"⚡️ Пиковые часы сегодня: <code>{biz_metrics['peak_hours']}</code>\n"
                f"🥋 Главное направление: <code>{human_discipline_name}</code>\n"
                f"💎 Действующих абонементов: <code>{active_passes}</code>\n\n"
                f"🚨 <b>МЕНЕДЖМЕНТ (Проверить админа):</b>\n"
                f"❌ Закончился баланс: <code>{expired_count} чел.</code> (ждут звонка)\n"
                f"last_visit 💤 Спящие (>14 дней): <code>{sleeping_count} чел.</code>\n"
            )

            # Отправляем инлайн-отчет напрямую директору клуба
            await bot.send_message(club.owner_id, report_text, parse_mode="HTML")
            logger.info(f"🔥 Комплексный ИИ-отчет для клуба {club.id} успешно отправлен боссу!")

            await asyncio.sleep(0.05)  # Защита от лимитов (Anti-flood API)

        except Exception as e:
            logger.error(f"❌ Ошибка генерации ИИ-отчета для клуба {club.id}: {e}")


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

        # Ежедневно напоминаем родителям о незаполненной дате рождения студента.
        # Один родитель получает одно сообщение со списком, а не отдельное сообщение на каждого ребёнка.
        missing_birthdays = {}
        for student in students:
            if student.parent_id and not student.birthday:
                key = (student.club_id, student.parent_id)
                missing_birthdays.setdefault(key, []).append(student.name)
        missing_club_ids = {club_id for club_id, _ in missing_birthdays}
        clubs_result = await session.execute(select(Club).where(Club.id.in_(missing_club_ids))) if missing_club_ids else None
        clubs_by_id = {club.id: club for club in clubs_result.scalars().all()} if clubs_result else {}
        for (club_id, parent_id), names in missing_birthdays.items():
            club = clubs_by_id.get(club_id)
            if not club or club.bot_token not in bots_dict:
                continue
            try:
                await bots_dict[club.bot_token].send_message(
                    chat_id=parent_id,
                    text=(f"🎂 <b>Заполните даты рождения атлетов</b>\n\n"
                          f"В профиле клуба <b>{club.name}</b> не указана дата рождения: "
                          f"<b>{', '.join(names)}</b>.\n"
                          "Это нужно для корректного возраста, тарифов и статистики."),
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                        types.InlineKeyboardButton(text="Указать дату рождения", callback_data="edit_birthday")
                    ]]),
                    parse_mode="HTML",
                )
            except Exception as reminder_error:
                logger.error("Не удалось отправить напоминание о ДР родителю %s: %s", parent_id, reminder_error)

        # ФИКС: Берем только чистую текущую дату (без часов и минут)
        today = datetime.now().date()
        now_datetime = datetime.now() # Оставляем datetime для вычитания из last_visit

        for student in students:
            # Вытаскиваем токен клуба этого студента для отправки сообщения
            club_result = await session.execute(select(Club).where(Club.id == student.club_id))
            club = club_result.scalar_one_or_none()
            if not club or club.bot_token not in bots_dict:
                continue
            bot = bots_dict[club.bot_token]

            # Если у студента еще нет привязанного Telegram ID (родитель не зашел в бота), пропускаем
            if not student.parent_id:
                continue

            # 🎂 Фича 1: Поздравление с Днем Рождения (Сверяем типы date == date)
            if hasattr(student, 'birthday') and student.birthday:
                if student.birthday.month == today.month and student.birthday.day == today.day:
                    try:
                        await bot.send_message(
                            chat_id=student.parent_id,
                            text=f"🎂 Клуб <b>{club.name}</b> поздравляет атлета <b>{student.name}</b> с Днём Рождения! 🎉\n"
                                 f"Желаем новых спортивных побед и крепкого здоровья!",
                            parse_mode="HTML"
                        )
                        logger.info(f"🎉 Поздравление с ДР отправлено атлету {student.name} (Родитель: {student.parent_id})")
                    except Exception as e:
                        logger.error(f"Не удалось отправить ДР сообщение родителю {student.parent_id}: {e}")

            # 🏃‍♂️ Фича 2: Контроль прогульщиков (Не было 10 дней, но есть активный абонемент)
            if student.last_visit and student.balance_lessons > 0 and not student.is_frozen:
                # student.last_visit — это datetime, так что вычитаем из такого же datetime
                days_absent = (now_datetime - student.last_visit).days
                if days_absent == 10:
                    try:
                        await bot.send_message(
                            chat_id=student.parent_id,
                            text=f"👋 Здравствуйте! Мы заметили, что атлет <b>{student.name}</b> не посещал тренировки уже 10 дней. Мы соскучились! Ждём вас на занятиях. 😉",
                            parse_mode="HTML"
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

#Рекурентные автоплатежи
async def saas_recurrent_payments_job(session_factory):
    """
    Ночная задача (APScheduler) для автоматического списания денег по подпискам ЮKassa.
    Защищена от DetachedInstanceError, ошибок таймзон и конфликтов транзакций.
    """
    logger.info("⏳ Запуск проверки рекуррентных платежей ЮKassa...")

    # Работаем в наивном формате UTC (как в базе данных на Аэзе) для защиты от TypeError
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    # Открываем одну сессию на всю крон-задачу
    async with session_factory() as session:
        # 1. Выбираем все активные подписки, у которых наступила дата списания
        result = await session.execute(
            select(Subscription)
            .where(Subscription.is_active == True)
            .where(Subscription.next_charge_at <= now_naive)
            .where(Subscription.rebill_id.is_not(None))
        )
        subscriptions = result.scalars().all()

        if not subscriptions:
            logger.info("✅ Нет подписок ЮKassa для списания на сегодня.")
            return

        for sub in subscriptions:
            # Генерация уникального OrderId для этого месяца продления
            order_id = f"REC_{uuid.uuid4().hex[:12].upper()}"

            # Подтягиваем клуб, чтобы достать его платежные ключи из JSONB
            club_result = await session.execute(select(Club).where(Club.id == sub.club_id))
            club = club_result.scalar_one_or_none()
            if not club:
                logger.error(f"🚨 Клуб с ID {sub.club_id} не найден в базе данных!")
                continue

            pay_settings = club.club_settings.get("payments", {})
            shop_id = pay_settings.get("yookassa_shop_id")
            secret_key = pay_settings.get("yookassa_secret_key")

            # Проверяем, настроил ли клуб интеграцию и включена ли она
            # ⚡ ИСПРАВЛЕНО:features.get("online_payments") убрали, так как проверяем ключи напрямую
            if not shop_id or not secret_key:
                logger.warning(f"⚠️ У клуба '{club.name}' не заполнены ключи ЮKassa для автосписания.")
                continue

            # Ищем студента, которому будем продлевать абонемент
            student_result = await session.execute(select(Student).where(Student.id == sub.student_id))
            student = student_result.scalar_one_or_none()
            if not student:
                logger.error(f"🚨 Атлет с ID {sub.student_id} подписки {sub.id} не найден!")
                continue

            # В Subscription хранится карта и сумма, но не параметры тарифа.
            # Восстанавливаем их из последнего подтверждённого заказа.
            latest_order_result = await session.execute(
                select(PaymentOrder)
                .where(
                    PaymentOrder.student_id == sub.student_id,
                    PaymentOrder.club_id == sub.club_id,
                    PaymentOrder.status == "CONFIRMED",
                    PaymentOrder.type.notlike("FREEZE%"),
                )
                .order_by(PaymentOrder.created_at.desc())
                .limit(1)
            )
            latest_order = latest_order_result.scalar_one_or_none()
            lesson_count = latest_order.lesson_count if latest_order else 8
            days_to_add = latest_order.days_to_add if latest_order and latest_order.days_to_add else 30
            discipline = latest_order.discipline if latest_order and latest_order.discipline else student.discipline

            # Фиксируем попытку списания в базу данных (внутри текущей транзакции, БЕЗ commit)
            new_order = PaymentOrder(
                id=order_id,
                user_id=sub.user_id,
                student_id=sub.student_id,
                club_id=sub.club_id,
                amount_kopecks=sub.amount_kopecks,
                lesson_count=lesson_count,
                days_to_add=days_to_add,
                discipline=discipline,
                status="NEW",
                type="RECURRENT"
            )
            session.add(new_order)

            # Делаем flush, чтобы SQLAlchemy отправила заказ в базу, но не закрывала транзакцию коммитом
            await session.flush()

            try:
                # 2. Инициализируем клиент ЮKassa ключами этого клуба и прокидываем прокси
                from config import PROXY_URL
                from services.yookassa_client import YooKassaClient

                yookassa_node = YooKassaClient(shop_id=shop_id, secret_key=secret_key, proxy_url=PROXY_URL)

                # Стучимся в ЮKassa за безакцептным автосписанием (rebill_id)
                charge_res = await yookassa_node.charge_payment(
                    order_id=order_id,
                    amount_kopecks=sub.amount_kopecks,
                    payment_method_id=sub.rebill_id,
                    club_name=club.name
                )

                # Вытаскиваем бота (убедись, что bots_dict импортирован или доступен в глобальной области)
                bot = bots_dict.get(club.bot_token) if 'bots_dict' in globals() or 'bots_dict' in locals() else None

                # 3. ЮKassa при успешном рекуррентном платеже возвращает статус 'succeeded' и код 200
                if charge_res.get("Success") and charge_res.get("Status") == "succeeded":
                    new_order.status = "CONFIRMED"

                    # Сдвигаем дату следующего списания на срок тарифа.
                    sub.next_charge_at = now_naive + timedelta(days=days_to_add)

                    # Продлеваем абонемент студенту в Postgres
                    current_expire = student.expire_date.replace(tzinfo=None) if student.expire_date else now_naive
                    base_date = current_expire if current_expire > now_naive else now_naive

                    student.expire_date = base_date + timedelta(days=days_to_add)

                    # Зачисляем количество занятий, привязанных к этому тарифу подписки
                    if student.balance_lessons != 999:
                        student.balance_lessons += new_order.lesson_count

                    logger.info(
                        f"💰 Успешное автосписание {sub.amount_kopecks / 100} руб для пользователя {sub.user_id}")

                    if bot:
                        try:
                            await bot.send_message(
                                chat_id=sub.user_id,
                                text=f"✨ <b>Подписка успешно продлена!</b>\n\n"
                                     f"Сумма <b>{sub.amount_kopecks / 100}₽</b> успешно списана с вашей карты.\n"
                                     f"Абонемент атлета <b>{student.name}</b> обновлен на {days_to_add} дней.\n"
                                     f"Зачислено занятий: <b>+{new_order.lesson_count} зан.</b>\n\n"
                                     f"Приятных тренировок! 💪",
                                parse_mode="HTML"
                            )
                            if config.get("owner_id"):
                                await bot.send_message(
                                    chat_id=int(config["owner_id"]),
                                    text=(
                                        "🔄 <b>Автопродление абонемента</b>\n\n"
                                        f"Атлет: <b>{escape(student.name)}</b>\n"
                                        f"Сумма: <b>{sub.amount_kopecks / 100:.2f} ₽</b>\n"
                                        f"Продлено до: <b>{student.expire_date.strftime('%d.%m.%Y')}</b>"
                                    ),
                                    parse_mode="HTML",
                                )
                        except Exception as b_err:
                            logger.error(f"Не удалось отправить уведомление об автопродлении: {b_err}")
                else:
                    # Ошибка списания со стороны ЮKassa (нет денег на карте, карта заблокирована)
                    new_order.status = "REJECTED"
                    sub.is_active = False  # Отключаем подписку, пока родитель не перепривяжет карту новой оплатой

                    logger.warning(f"❌ ЮKassa отклонила автосписание для {sub.user_id}: {charge_res.get('Message')}")

                    if bot:
                        try:
                            await bot.send_message(
                                chat_id=sub.user_id,
                                text="⚠️ <b>Ошибка автопродления подписки</b>\n\n"
                                     f"Не удалось автоматически списать средства за абонемент атлета <b>{student.name}</b>.\n"
                                      "Пожалуйста, проверьте баланс карты или оплатите абонемент заново в меню бота, чтобы привязать актуальную карту."
                            )
                            if config.get("owner_id"):
                                await bot.send_message(
                                    chat_id=int(config["owner_id"]),
                                    text=(
                                        "⚠️ <b>Не удалось автопродлить абонемент</b>\n\n"
                                        f"Атлет: <b>{escape(student.name)}</b>\n"
                                        f"Клиент ID: <code>{sub.user_id}</code>\n"
                                        "Подписка отключена до повторной оплаты."
                                    ),
                                    parse_mode="HTML",
                                )
                        except Exception as b_err:
                            logger.error(f"Не удалось отправить уведомление об отказе рекуррента: {b_err}")

                # Коммитим текущую итерацию цикла в Postgres на Аэзе
                await session.commit()

            except Exception as e:
                await session.rollback()
                logger.error(f"🚨 Ошибка при обработке рекуррента для sub_id {sub.id}: {repr(e)}")

#Авто закрытие сесии
async def auto_close_sessions_job():
    """
    Фоновая задача для APScheduler. Запускается каждую минуту.
    Сравнивает UTC время базы с UTC сервера. Смещение +3 делает только для ТГ-сообщений.
    """
    global bots_dict

    async with AsyncSessionLocal() as db:
        try:
            clubs_res = await db.execute(select(Club))
            clubs = clubs_res.scalars().all()

            club_configs = {}
            for club in clubs:
                club_settings = club.club_settings or {}
                timeout = club_settings.get("limits", {}).get("session_timeout_minutes", 150)
                club_configs[club.id] = {
                    "timeout": timeout,
                    "bot_token": club.bot_token,
                    "owner_id": club.owner_id,
                    "club_name": club.name
                }

            # СЕРВЕРНОЕ ВРЕМЯ: Берем чистый наивный UTC (как и хендлеры прохода в базу)
            now_server_utc = datetime.now(timezone.utc).replace(tzinfo=None)

            query = select(Student).where(Student.last_visit != None).with_for_update()
            res = await db.execute(query)
            students_in_gym = res.scalars().all()

            for student in students_in_gym:
                config = club_configs.get(student.club_id)
                if not config:
                    continue

                timeout_minutes = config["timeout"]

                # Читаем UTC из базы и сравниваем с UTC сервера. Разница будет идеальной!
                last_visit_naive = student.last_visit.replace(tzinfo=None) if student.last_visit else now_server_utc
                time_passed = now_server_utc - last_visit_naive

                # ЕСЛИ ВРЕМЯ СЕССИИ ИСТЕКЛО:
                if time_passed >= timedelta(minutes=timeout_minutes):
                    logger.info(f"⏱ Время сессии истекло ({timeout_minutes} мин) для атлета {student.name}")

                    is_unlimited = (student.balance_lessons == 999)
                    if not is_unlimited:
                        student.balance_lessons = max(0, (student.balance_lessons or 0) - 1)

                    current_balance = student.balance_lessons
                    student_name = student.name
                    parent_id = student.parent_id

                    # Закрываем сессию визита в базе
                    student.last_visit = None

                    # КРАСИВОЕ ВРЕМЯ ДЛЯ ТГ: Прибавляем +3 часа к UTC базы только для вывода текста людям!
                    visit_moscow = last_visit_naive + timedelta(hours=3)
                    visit_str = visit_moscow.strftime("%H:%M")

                    bot = bots_dict.get(config["bot_token"])
                    if bot:
                        balance_text = "♾ Безлимит" if is_unlimited else f"{current_balance} зан."

                        # А) Уведомление родителю
                        if parent_id:
                            try:
                                await bot.send_message(
                                    chat_id=int(parent_id),
                                    text=f"🏁 <b>Тренировка завершена!</b>\n\n"
                                         f"Атлет <b>{student_name}</b> покинул зал.\n"
                                         f"⏱ Вход был в: {visit_str} (МСК)\n"
                                         f"⏱ Длительность сессии: {timeout_minutes} мин.\n"
                                         f"📉 Списано: 1 занятие.\n"
                                         f"🔢 Остаток на балансе: <b>{balance_text}</b>",
                                    parse_mode="HTML"
                                )
                            except Exception as e_msg:
                                logger.warning(f"Не удалось отправить ТГ-уведомление родителю {parent_id}: {e_msg}")

                        # Б) Уведомление владельцу клуба
                        if config["owner_id"]:
                            try:
                                await bot.send_message(
                                    chat_id=int(config["owner_id"]),
                                    text=f"📝 <b>Автозакрытие сессии визита</b>\n\n"
                                         f"Клуб: <b>{config['club_name']}</b>\n"
                                         f"Атлет: <b>{student_name}</b>\n"
                                         f"Вход зафиксирован в: {visit_str}\n"
                                         f"Сессия закрыта автоматически через {timeout_minutes} мин.\n"
                                         f"Баланс в базе успешно обновлен: <b>{balance_text}</b>",
                                    parse_mode="HTML"
                                )
                            except Exception as e_adm:
                                logger.warning(f"Не удалось отправить ТГ-алерт админу {config['owner_id']}: {e_adm}")

            await db.commit()

        except Exception as cron_err:
            logger.error(f"❌ Ошибка в кроне автосписаний: {cron_err}", exc_info=True)
            await db.rollback()



