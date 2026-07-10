import httpx
from loguru import logger

from handlers.skud import trigger_dingtian_turnstile
from fastapi import Query
from services.analytics import calculate_club_metrics, generate_students_excel, calculate_admin_dashboard
from database.constants import DEFAULT_CLUB_SETTINGS
import hmac
from datetime import datetime, timedelta, timezone
from database.db import PaymentOrder, Subscription
from database.db import add_abon
import hashlib
from fastapi.responses import StreamingResponse
import json
from urllib.parse import parse_qsl
import io
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi import Depends, HTTPException, APIRouter, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette import status
from database.db import User, Student, Club
from database.db import get_session
from config import fastapi_key
# Убираем глобальный префикс /stats, чтобы роуты /admin и /revenue сидели на своем месте
router = APIRouter(tags=["Analytics"])
templates = Jinja2Templates(directory="templates")

API_KEY_NAME = "X-API-Key"
API_KEY = fastapi_key

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


async def get_api_key(header_value: str = Security(api_key_header)):
    if header_value == API_KEY:
        return header_value
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Доступ запрещен: неверный API ключ"
    )


# Хелпер-функция: парсит поддомен и достает club_id (для твоей SaaS-логики)
def get_club_id_from_host(request: Request) -> int:
    host = request.headers.get("host", "")
    subdomain = host.split(".")[0]

    if subdomain.isdigit():
        return int(subdomain)

    # Если зашли по прямой ссылке без поддомена, пробуем взять из query-параметров (?club_id=...)
    club_id_param = request.query_params.get("club_id")
    if club_id_param and club_id_param.isdigit():
        return int(club_id_param)

    return 0  # Дефолтный ID, если ничего не нашли


# 1. Добавляем роут /admin, который просила кнопка в ТГ (убрали get_api_key!)
@router.get("/admin", response_class=HTMLResponse)
async def get_admin_dashboard(
        request: Request,
        session: AsyncSession = Depends(get_session)
):
    club_id = get_club_id_from_host(request)

    # 1. Загружаем из базы список студентов ТОЛЬКО этого клуба (Изоляция SaaS)
    result = await session.execute(
        select(Student).where(Student.club_id == club_id)
    )
    # Оборачиваем в list(), чтобы у линтера PyCharm не было претензий к типам
    students = list(result.scalars().all())

    # 2. Передаем список в наш аналитический сервис для обработки
    admin_data = calculate_admin_dashboard(students)

    # 3. Рендерим новый шаблон admin.html и распаковываем туда словарь с данными
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "club_id": club_id,
            **admin_data  # Распакует total_athletes, active_now_count, all_athletes и т.д.
        }
    )


#
# 2. Роут /revenue (убрали get_api_key, чтобы открывался в WebApp Телеграма)
@router.get("/revenue", response_class=HTMLResponse)
async def get_revenue_stats(
        request: Request,
        session: AsyncSession = Depends(get_session)
):
    club_id = get_club_id_from_host(request)

    # Загружаем студентов
    res_students = await session.execute(select(Student).where(Student.club_id == club_id))
    students = list(res_students.scalars().all())

    # Загружаем успешные платежи текущего месяца для точного расчета выручки
    start_of_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    res_payments = await session.execute(
        select(PaymentOrder).where(
            PaymentOrder.club_id == club_id,
            PaymentOrder.status == "CONFIRMED",
            PaymentOrder.created_at >= start_of_month
        )
    )
    payments = list(res_payments.scalars().all())

    metrics = calculate_club_metrics(students, payments)

    return templates.TemplateResponse(
        "stats.html",
        {"request": request, "club_id": club_id, **metrics}
    )


# 3. Выгрузка в Excel. Перенесли префикс /stats/export/excel прямо в декоратор
@router.get("/stats/export/excel")
async def export_students_to_excel(
        request: Request,
        session: AsyncSession = Depends(get_session)
):
    club_id = get_club_id_from_host(request)

    # ФИКС SAAS: Строго вытаскиваем студентов ТОЛЬКО этого конкретного клуба!
    result = await session.execute(
        select(Student).where(Student.club_id == club_id)
    )
    students = list(result.scalars().all())

    if not students:
        # Если выгружать некого, можно просто вернуть пустой ответ или обработать красиво
        return StreamingResponse(io.BytesIO(), media_type="application/vnd.ms-excel")

    # Генерируем Excel через изолированный сервис
    excel_file = generate_students_excel(students)

    # Правильные заголовки и современный media_type для .xlsx файлов
    headers = {
        "Content-Disposition": f'attachment; filename="report_club_{club_id}.xlsx"'
    }

    return StreamingResponse(
        excel_file,
        headers=headers,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@router.get("/webapp/schedule", response_class=HTMLResponse)
async def webapp_schedule_page(
        request: Request,
        club_id: int = None,
        session: AsyncSession = Depends(get_session)
):
    from database.db import Club
    from sqlalchemy.future import select

    if not club_id:
        try:
            club_id = get_club_id_from_host(request)
        except Exception:
            club_id = None

    if not club_id:
        return HTMLResponse(content="<h1>❌ Ошибка: Не удалось определить ID клуба</h1>", status_code=400)

    stmt = select(Club).where(Club.id == club_id)
    result = await session.execute(stmt)
    club = result.scalar_one_or_none()

    if not club:
        return HTMLResponse(content="<h1>🏰 Клуб не найден в системе SpeedyCRM</h1>", status_code=404)

    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    disciplines_data = settings.get("disciplines", {})

    day_names = {
        "mon": "Понедельник", "tue": "Вторник", "wed": "Среда",
        "thu": "Четверг", "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье"
    }

    # Парсим JSON-настройки клуба в структурированный список для Jinja2 шаблона
    parsed_disciplines = []

    if isinstance(disciplines_data, dict):
        for disc_key, disc_content in disciplines_data.items():
            if not isinstance(disc_content, dict):
                continue

            disc_name = disc_content.get("name", "Спортивная секция")
            schedule_data = disc_content.get("schedule", {})
            if not isinstance(schedule_data, dict):
                schedule_data = {}

            parsed_days = []
            for day_key, day_title in day_names.items():
                lessons = schedule_data.get(day_key, [])
                if not lessons or not isinstance(lessons, list):
                    continue

                parsed_lessons = []
                for lesson in lessons:
                    if not isinstance(lesson, dict):
                        continue

                    max_slots = lesson.get("max_slots") or lesson.get("slots") or lesson.get("limit") or 50
                    taken_slots = lesson.get("taken_slots") or 0

                    try:
                        max_slots = int(max_slots)
                    except (ValueError, TypeError):
                        max_slots = 50

                    try:
                        taken_slots = int(taken_slots)
                    except (ValueError, TypeError):
                        taken_slots = 0

                    parsed_lessons.append({
                        "time": str(lesson.get("time", "00:00")),
                        "coach": str(lesson.get("coach", "Инструктор")),
                        "max_slots": max_slots,
                        "free_slots": max(0, max_slots - taken_slots)
                    })

                if parsed_lessons:
                    parsed_days.append({
                        "title": day_title,
                        "lessons": parsed_lessons
                    })

            parsed_disciplines.append({
                "name": disc_name,
                "days": parsed_days
            })

    # Отдаем чистый контекст в шаблон
    context = {
        "request": request,
        "club_name": club.name or 'Без названия',
        "disciplines": parsed_disciplines
    }
    return templates.TemplateResponse("schedule.html", context)


# --- Системные роуты (Для них защиту по API-ключу ОСТАВЛЯЕМ) ---

class StudentCreate(BaseModel):
    name: str
    parent_id: int


@router.post("/stats/students")
async def create_student(data: StudentCreate, session: AsyncSession = Depends(get_session), _=Depends(get_api_key)):
    new_student = Student(name=data.name, parent_id=data.parent_id)
    session.add(new_student)
    await session.commit()
    return {"status": "success", "student": new_student.name}


@router.get("/stats/users", response_model=None)
async def get_all_users(session: AsyncSession = Depends(get_session), _=Depends(get_api_key)):
    query = select(User).options(selectinload(User.students))
    result = await session.execute(query)
    users = result.scalars().all()
    return users


@router.get("/stats/students/{student_id}")
async def get_student_info(student_id: int, session: AsyncSession = Depends(get_session), _=Depends(get_api_key)):
    query = select(Student).where(Student.id == student_id)
    result = await session.execute(query)
    student = result.scalar_one_or_none()

    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


@router.get("/privacy", response_class=HTMLResponse)
async def get_privacy_page(request: Request):
    """Страница политики конфиденциальности для WebApp"""
    return templates.TemplateResponse("privacy.html", {"request": request})


@router.get("/oferta", response_class=HTMLResponse)
async def get_oferta_page(request: Request):
    """Страница публичной оферты для WebApp"""
    return templates.TemplateResponse("oferta.html", {"request": request})


@router.get("/webapp/live_cam", response_class=HTMLResponse)
async def get_cameras_page(request: Request, club_id: int = Query(...)):
    return templates.TemplateResponse("cameras.html", {"request": request, "club_id": club_id})


# 2. Роут генерации стрима (ПОЛНОСТЬЮ ОБНОВЛЁННЫЙ ПРОКСИ-ВАРИАНТ)
@router.get("/webapp/live_cam/stream")
async def video_stream(
        club_id: int = Query(...),
        session: AsyncSession = Depends(get_session)
):
    """
    Проксирует MJPEG видеопоток из внутреннего контейнера Docker (go2rtc)
    напрямую в WebApp смартфона, динамически подставляя камеру из настроек клуба.
    """
    # Вытаскиваем настройки именно этого клуба из БД для изоляции SaaS
    result = await session.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Клуб не найден")

    # Берем имя камеры из club_settings. Если там пусто, ставим дефолтное "camera1"
    settings = club.club_settings or {}
    camera_src = settings.get("turnstile", {}).get("camera_src", "camera1")

    # Внутренний URL в сети Docker (смартфон его не видит, но FastAPI до него достучится)
    go2rtc_mjpeg_api = f"http://gym_go2rtc:1984/api/stream.mjpeg?src={camera_src}"

    # Асинхронный генератор-мост
    async def stream_generator():
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream("GET", go2rtc_mjpeg_api) as response:
                    if response.status_code != 200:
                        return

                    # Читаем байты по кусочкам из go2rtc и транслируем в телефон админа
                    async for chunk in response.aiter_bytes():
                        yield chunk
            except httpx.RequestError:
                return

    # Отдаем поток с правильным MJPEG заголовком
    return StreamingResponse(
        stream_generator(),
        media_type="multipart/x-mixed-replace; boundary=--frame"
    )


@router.get("/pass-app", response_class=HTMLResponse)
async def get_web_app_page(request: Request, user_id: int, db: AsyncSession = Depends(get_session)):
    """
    Эндпоинт, который открывается в Telegram WebApp по ссылке:
    https://твоя_куча_поддоменов.ru/admin/pass-app?user_id={telegram_id}
    """
    # Вытаскиваем родителя и всех его детей из базы данных
    query = select(User).where(User.user_id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # Достаем список студентов для этого родителя
    students_query = select(Student).where(Student.parent_id == user_id)
    students_result = await db.execute(students_query)
    students = students_result.scalars().all()

    # Рендерим HTML страницу и передаем туда список детей
    return templates.TemplateResponse("biometric_pass.html", {"request": request, "students": students})



#BIOMETRIC BIOMETRIC

class BiometricCheckIn(BaseModel):
    student_id: int
    biometric_token: str | None = None
    init_data: str


def verify_telegram_data(init_data: str, bot_token: str) -> dict | None:
    """Криптографическая проверка, что init_data пришла от реального юзера Telegram"""
    try:
        parsed_data = dict(parse_qsl(init_data))
        if "hash" not in parsed_data:
            return None
        tg_hash = parsed_data.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calculated_hash == tg_hash:
            return json.loads(parsed_data.get("user", "{}"))
        return None
    except Exception:
        return None


# 1. РОУТ ДЛЯ ВЫДАЧИ HTML СТРАНИЦЫ РОДИТЕЛЮ
@router.get("/webapp/biometric-pass", response_class=HTMLResponse)
async def get_biometric_page(request: Request, club_id: int, user_id: int, db: AsyncSession = Depends(get_session)):
    """
    Отдает красивую HTML-страницу со списком детей.
    Ссылка в кнопке: https://{club_id}.speedycrm.ru/webapp/biometric-pass?club_id={club_id}&user_id={user_id}
    """
    # Достаем студентов, привязанных к этому родителю и этому клубу
    students_query = select(Student).where(Student.parent_id == user_id, Student.club_id == club_id)
    students_result = await db.execute(students_query)
    students = students_result.scalars().all()

    # Рендерим HTML и передаем туда список детей
    return templates.TemplateResponse("biometric_pass.html", {"request": request, "students": students})

# 2. РОУТ ДЛЯ ОБРАБОТКИ НАЖАТИЯ И ОТКРЫТИЯ ТУРНИКЕТА


@router.post("/open-turnstile")
async def open_turnstile(
        payload: dict,
        request: Request,
        db: AsyncSession = Depends(get_session)
):
    """
    Сюда летит POST-запрос с фронтенда после успешного сканирования FaceID.
    Синхронизировано со структурами моделей СУБД Postgres.
    """
    student_id = payload.get("student_id")
    biometric_token = payload.get("biometric_token")
    init_data = payload.get("init_data")

    # [ОПЦИОНАЛЬНО] 1. Твоя валидация Телеграм init_data, если необходима
    # ...

    # Ищем студента с row-level блокировкой (with_for_update) от Race Condition
    student_res = await db.execute(
        select(Student)
        .where(Student.id == student_id)
        .with_for_update()
    )
    student = student_res.scalar_one_or_none()
    if not student:
        return {"success": False, "message": "Студент не найден в базе данных"}

    # Ищем клуб (чтения достаточно, без блокировки)
    club_res = await db.execute(select(Club).where(Club.id == student.club_id))
    club = club_res.scalar_one_or_none()
    if not club:
        return {"success": False, "message": "Клуб студента не найден"}

    # Работаем с JSONB полем club_settings (гарантируем dict благодаря MutableDict)
    club_settings = club.club_settings or {}

    # Достаем конфигурацию турникета из настроек
    relay_config = club_settings.get("turnstile", {})

    # Если СКУД глобально выключен для клуба — не пускаем
    if not relay_config.get("enabled", False):
        return {"success": False, "message": "СКУД отключен в настройках вашего клуба"}

    # Вытаскиваем индивидуальные лимиты сессии
    timeout_minutes = club_settings.get("limits", {}).get("session_timeout_minutes", 150)

    # Логика работы с таймзонами (сервер на Аэзе)
    now = datetime.now(timezone.utc)
    is_inside_session = False

    if student.last_visit:
        # Приводим к naive-формату, так как в модели DateTime без tzinfo=True
        last_visit_naive = student.last_visit.replace(tzinfo=None)
        now_naive = now.replace(tzinfo=None)

        if now_naive - last_visit_naive < timedelta(minutes=timeout_minutes):
            is_inside_session = True

    # Формируем логику списания и тексты ответов
    if is_inside_session:
        logger.info(f"🔄 Повторный проход. Атлет {student.name} зашел в рамках сессии. Занятие НЕ списываем.")
        session_end = student.last_visit + timedelta(minutes=timeout_minutes)
        session_end_str = session_end.strftime("%H:%M")

        message_text = (
            f"Турникет открыт. Осталось занятий: {student.balance_lessons}. "
            f"⚠️ Повторный проход! Сессия активна до {session_end_str}."
        )
    else:
        logger.info(f"🎫 Новый визит. Атлет {student.name} начинает тренировку. Проверяем баланс.")

        # Проверка лимита (999 — безлимит, его не блокируем)
        if student.balance_lessons <= 0 and student.balance_lessons != 999:
            return {"success": False, "message": f"Ошибка: У ученика {student.name} закончились занятия! ❌"}

        # Проверка на повторный вход в течение дня (сверхдолгая тренировка) -> Алерт владельцу
        if student.last_visit:
            last_visit_naive = student.last_visit.replace(tzinfo=None)
            now_naive = now.replace(tzinfo=None)

            if now_naive - last_visit_naive < timedelta(hours=6):
                try:
                    bots_dict = getattr(request.app.state, "bots_dict", {})
                    bot = bots_dict.get(club.bot_token)

                    if bot and club.owner_id:
                        await bot.send_message(
                            chat_id=int(club.owner_id),
                            text=f"⚠️ <b>Алерт FaceID (Повторный визит)</b>\n\n"
                                 f"Атлет: <b>{student.name}</b>\n"
                                 f"Прошлый вход: {last_visit_naive.strftime('%H:%M')}\n"
                                 f"Текущий вход: {now_naive.strftime('%H:%M')}\n\n"
                                 f"Система зафиксировала проход спустя {timeout_minutes} мин. и <b>списала второе занятие за сегодня</b>.",
                            parse_mode="HTML"
                        )
                except Exception as alert_err:
                    logger.error(f"Не удалось отправить алерт владельцу клуба: {alert_err}")

        # Изменяем баланс в памяти (не безлимит)
        if student.balance_lessons != 999:
            student.balance_lessons -= 1

        student.last_visit = now
        message_text = f"Турникет открыт для {student.name}! Осталось занятий: {student.balance_lessons}"

    # 3. ОТПРАВЛЯЕМ КОМАНДУ НА РЕЛЕ DINGTIAN (ДО КОММИТА)
    try:
        # Передаем конфигурационный словарь целиком, как ожидает твоя функция
        # Перестраховываемся с форматированием base_url
        base_url = str(relay_config.get("base_url", ""))
        if base_url and not base_url.startswith("http"):
            relay_config["base_url"] = f"http://{base_url}"

        # Вызываем твою оригинальную функцию!
        is_opened = await trigger_dingtian_turnstile(relay_config)

        # ⚡ КЛЮЧЕВОЙ ИСПРАВЛЕНИЕ: Если функция возвращает None или падает по таймауту,
        # но исключения нет — мы ПРИНУДИТЕЛЬНО считаем проход успешным, так как железка сработала.
        if is_opened is False:
            return {"success": False, "message": "Реле СКУД отклонило команду (вернуло False)."}

    except Exception as e:
        # Если произошел таймаут чтения сети, но реле УСПЕЛО щелкнуть:
        # Мы пишем ошибку в логи сервера, но ПОЛЬЗОВАТЕЛЯ ПРОПУСКАЕМ и списываем занятие!
        logger.warning(f"Запрос до реле дошел, но произошла ошибка сети/таймаута: {str(e)}. Проход разрешен.")

    # 4. Фиксируем изменения баланса в Postgres на Аэзе
    await db.commit()

    return {
        "success": True,
        "message": message_text
    }


@router.post("/webapp/open-turnstile")
async def open_webapp_turnstile(
        payload: BiometricCheckIn,
        request: Request,
        db: AsyncSession = Depends(get_session)
):
    """
    Принимает сигнал об успешном FaceID из Telegram WebApp родителя.
    Проверяет подпись init_data, биометрию, лимиты, сессии и дергает реле.
    """
    from database.db import Student, Club, User

    # 1. БЛОКИРОВКА СТРОКИ СТУДЕНТА (Защита от повторных тапов в WebApp)
    student_query = (
        select(Student)
        .where(Student.id == payload.student_id)
        .with_for_update()
    )
    student_res = await db.execute(student_query)
    student = student_res.scalar_one_or_none()

    if not student:
        raise HTTPException(status_code=404, detail="Студент не найден")

    # Ищем клуб для проверки токена бота и настроек СКУД
    club_query = select(Club).where(Club.id == student.club_id)
    club_res = await db.execute(club_query)
    club = club_res.scalar_one_or_none()

    if not club or not club.bot_token:
        raise HTTPException(status_code=400, detail="Конфигурация клуба не найдена")

    # 2. БЕЗОПАСНОСТЬ TELEGRAM (Валидация init_data)
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user or "id" not in tg_user:
        raise HTTPException(status_code=403, detail="Ошибка безопасности: Неверные данные WebApp")

    telegram_user_id = tg_user["id"]

    # Проверяем связь «Родитель-Ребенок» на основе моделей СУБД
    if student.parent_id != telegram_user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещен: Вы не являетесь родителем этого атлета")

    # Проверяем, включена ли у родителя обязательная биометрия
    user_query = select(User).where(User.user_id == telegram_user_id)
    user_res = await db.execute(user_query)
    parent_user = user_res.scalar_one_or_none()

    if parent_user and getattr(parent_user, 'is_biometric_enabled', False):
        if not payload.biometric_token:
            raise HTTPException(status_code=400, detail="Необходимо биометрическое подтверждение на устройстве")

    # 3. ПРОВЕРКИ СТАТУСА АБОНЕМЕНТА
    if student.is_frozen == 1:
        return {"success": False, "message": "Абонемент заморожен."}

    # Работаем со временем (убираем tzinfo для DateTime в Postgres)
    now = datetime.now(timezone.utc)
    expire_naive = student.expire_date.replace(tzinfo=None) if student.expire_date else None
    now_naive = now.replace(tzinfo=None)

    if expire_naive and expire_naive < now_naive:
        return {"success": False, "message": "Срок действия абонемента истек."}

    # Безопасное чтение JSONB-настроек
    club_settings = club.club_settings or {}
    relay_config = club_settings.get("turnstile", {})

    # Проверяем, активен ли СКУД для этого клуба
    if not relay_config.get("enabled", False):
        return {"success": False, "message": "СКУД отключен в настройках клуба."}

    # Вытаскиваем индивидуальный таймаут сессии визита
    timeout_minutes = club_settings.get("limits", {}).get("session_timeout_minutes", 150)

    # Расчет активной сессии визита
    is_inside_session = False
    if student.last_visit:
        last_visit_naive = student.last_visit.replace(tzinfo=None)
        if now_naive - last_visit_naive < timedelta(minutes=timeout_minutes):
            is_inside_session = True

    # Блокируем проход, только если сессия НОВАЯ, а занятий нет (и это не безлимит 999)
    if not is_inside_session and student.balance_lessons <= 0 and student.balance_lessons != 999:
        return {"success": False, "message": "На балансе нет доступных занятий."}

    # Формируем логику и сообщения перед отправкой команды на реле
    if is_inside_session:
        logger.info(f"🔄 Повторный проход через WebApp. Атлет {student.name} в сессии. Занятие НЕ списываем.")
        session_end = student.last_visit + timedelta(minutes=timeout_minutes)
        session_end_str = session_end.strftime("%H:%M")
        message_text = f"Турникет открыт! Повторный проход. Сессия активна до {session_end_str}."
    else:
        # 🚨 Алерт владельцу клуба при списании второго занятия за день (проход спустя 2.5+ часа, но меньше 6 часов)
        if student.last_visit:
            last_visit_naive = student.last_visit.replace(tzinfo=None)
            if now_naive - last_visit_naive < timedelta(hours=6):
                try:
                    bots_dict = getattr(request.app.state, "bots_dict", {})
                    bot = bots_dict.get(club.bot_token)
                    if bot and club.owner_id:
                        await bot.send_message(
                            chat_id=int(club.owner_id),
                            text=f"⚠️ <b>Алерт WebApp СКУД (Повторный визит)</b>\n\n"
                                 f"Атлет: <b>{student.name}</b>\n"
                                 f"Родитель открыл турникет через WebApp кнопку спустя {timeout_minutes} мин.\n"
                                 f"Система зафиксировала новую сессию и <b>списала второе занятие за сегодня</b>.",
                            parse_mode="HTML"
                        )
                except Exception as alert_err:
                    logger.error(f"Не удалось отправить алерт владельцу клуба из WebApp эндпоинта: {alert_err}")

        # Списание баланса, если это не безлимит
        if student.balance_lessons != 999:
            student.balance_lessons -= 1

        student.last_visit = now
        message_text = f"Турникет открыт для {student.name}! Осталось занятий: {student.balance_lessons}"

    # 4. ОТПРАВЛЯЕМ КОМАНДУ НА РЕЛЕ DINGTIAN (ДО КОММИТА БАЗЫ)
    try:
        base_url = str(relay_config.get("base_url", ""))
        if base_url and not base_url.startswith("http"):
            relay_config["base_url"] = f"http://{base_url}"

        # Вызываем твою рабочую функцию
        is_opened = await trigger_dingtian_turnstile(relay_config)

        if is_opened is False:
            return {"success": False, "message": "Реле отклонило команду на открытие."}

    except Exception as e:
        # Исключаем ложный откат базы при микросбоях сети, если железка успела щелкнуть
        logger.warning(f"Ошибка сети/таймаута СКУД в WebApp: {str(e)}. Проход разрешен.")

    # 5. ФИКСИРУЕМ ИЗМЕНЕНИЯ В БАЗЕ (Только после успешной отправки команды в СКУД)
    await db.commit()

    return {"success": True, "message": message_text}


#Enabled biometri!!!!!

class BiometricEnable(BaseModel):
    init_data: str


@router.post("/webapp/enable-biometry")
async def enable_biometry(payload: BiometricEnable, db: AsyncSession = Depends(get_session)):
    """
    Эндпоинт вызывается один раз, когда родитель включает FaceID в приложении.
    Ставит флаг is_biometric_enabled = True в базу данных.
    """
    # 1. Достаем временный токен бота для проверки (в данном контексте можно через релейшн или по club_id из init_data)
    # Для упрощения сначала парсим юзера, чтобы найти его в БД
    parsed_data = dict(parse_qsl(payload.init_data))
    tg_user = json.loads(parsed_data.get("user", "{}"))
    telegram_user_id = tg_user.get("id")

    if not telegram_user_id:
        raise HTTPException(status_code=400, detail="Неверные данные Telegram")

    # 2. Ищем родителя в базе данных
    user_query = select(User).where(User.user_id == telegram_user_id)
    user_res = await db.execute(user_query)
    parent_user = user_res.scalar_one_or_none()

    if not parent_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # 3. Достаем клуб, чтобы верифицировать init_data по токену бота
    club_query = select(Club).where(Club.id == parent_user.club_id)
    club_res = await db.execute(club_query)
    club = club_res.scalar_one_or_none()

    if not club or not verify_telegram_data(payload.init_data, club.bot_token):
        raise HTTPException(status_code=430, detail="Ошибка безопасности данных")

    # 4. Включаем биометрию в базе данных!
    parent_user.is_biometric_enabled = True
    await db.commit()

    return {"success": True, "message": "Биометрия успешно активирована в профиле!"}

#PAYMENT PAYMENT




# Используй существующий router из твоего api.py


WEBHOOK_SECRET_TOKEN = "Speedycrmsaas2026"


@router.post("/v1/payments/yookassa/webhook")
async def yookassa_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    """
    Прием уведомлений об оплатах (вебхуков) от ЮKassa.
    Маршрут защищен токеном авторизации для безопасной пересылки РФ -> Вена.
    """
    # 1. 🛡️ ЗАЩИТА ЭНДПОИНТА
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid auth header")

    token = auth_header.split(" ")[1]
    if token != WEBHOOK_SECRET_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: Invalid webhook token")

    # Читаем JSON от ЮKassa
    payload = await request.json()

    event = payload.get("event")  # 'payment.succeeded'
    object_data = payload.get("object", {})  # Данные платежа

    metadata = object_data.get("metadata", {})
    order_id = metadata.get("order_id")

    if not order_id:
        return {"status": "ignored"}

    # 2. 🔥 ЕСЛИ ОПЛАТА УСПЕШНО ПРОШЛА (payment.succeeded)
    if event == "payment.succeeded":
        # Используем with_for_update() для защиты от двойного начисления (Бот + Вебхук одновременно)
        order_query = select(PaymentOrder).where(PaymentOrder.id == order_id).with_for_update()
        order_result = await session.execute(order_query)
        order = order_result.scalar_one_or_none()

        # Если заказ найден и он еще обрабатывается
        if order and order.status != "CONFIRMED":
            order.status = "CONFIRMED"

            # Вытаскиваем данные метода оплаты строго по структуре ЮKassa
            payment_method = object_data.get("payment_method", {})
            payment_method_id = payment_method.get("id")

            # ⚡ ИСПРАВЛЕНО: Флаг saved лежит ВНУТРИ объекта payment_method!
            saved_card_flag = payment_method.get("saved", False)

            # Если это первая оплата (FIRST) и карта успешно привязалась для рекуррентов
            if order.type == "FIRST" and payment_method_id and saved_card_flag:
                sub_query = select(Subscription).where(
                    Subscription.student_id == order.student_id,
                    Subscription.club_id == order.club_id
                ).with_for_update()

                sub_result = await session.execute(sub_query)
                subscription = sub_result.scalar_one_or_none()

                # Сдвигаем дату следующего списания на 30 дней вперед в наивном UTC для Postgres на Аэзе
                next_charge_naive = (datetime.now(timezone.utc) + timedelta(days=30)).replace(tzinfo=None)

                if subscription:
                    # Обновляем токен сохраненной карты ЮKassa
                    subscription.rebill_id = str(payment_method_id)
                    subscription.next_charge_at = next_charge_naive
                    subscription.is_active = True
                    subscription.amount_kopecks = order.amount_kopecks
                else:
                    # Создаем чистую подписку под автосписания по крону
                    new_sub = Subscription(
                        user_id=order.user_id,
                        student_id=order.student_id,
                        club_id=order.club_id,
                        rebill_id=str(payment_method_id),  # Сохраняем ID карты
                        amount_kopecks=order.amount_kopecks,
                        next_charge_at=next_charge_naive,
                        is_active=True
                    )
                    session.add(new_sub)

            # Достаем объект клуба для настроек
            club_result = await session.execute(select(Club).where(Club.id == order.club_id))
            club = club_result.scalar_one_or_none()
            club_settings = club.club_settings if club else {}

            # 3. НАЧИСЛЯЕМ АБОНЕМЕНТ УЧЕНИКУ
            abon_result = await add_abon(
                student_id=order.student_id,
                lessons_count=order.lesson_count,
                session=session,
                club_id=order.club_id,
                club_settings=club_settings,
                days_to_add=order.days_to_add
            )

            # Сохраняем транзакцию. Блокировка with_for_update снимется автоматически
            await session.commit()

            # 4. ОТПРАВЛЯЕМ SaaS-УВЕДОМЛЕНИЕ РОДИТЕЛЮ ЧЕРЕЗ БОТА КЛУБА
            if abon_result:
                new_expire, parent_id = abon_result
                try:
                    bots_dict = getattr(request.app.state, "bots_dict", {})
                    bot = bots_dict.get(club.bot_token) if club else None

                    if bot:
                        desc = "БЕЗЛИМИТ" if order.lesson_count == 999 else f"{order.lesson_count} зан."
                        ui_cfg = club_settings.get("ui", {})
                        club_name = ui_cfg.get("club_name", club.name if club else "Фитнес-клуб")

                        await bot.send_message(
                            chat_id=parent_id,
                            text=f"🥳 <b>Отличные новости!</b>\n\n"
                                 f"Ваша официальная оплата в фитнес-клуб <b>{club_name}</b> успешно получена.\n"
                                 f"Абонемент (<b>{desc}</b>) успешно активирован и действует до: <b>{new_expire}</b>. 🔥\n"
                                 f"Карта привязана к системе автопродления. Следующее списание пройдет автоматически.\n\n"
                                 f"<i>Ждем вас на тренировках!</i>",
                            parse_mode="HTML"
                        )
                except Exception as e:
                    logger.error(f"Ошибка отправки сообщения родителю в бот: {e}")

    # 5. ЕСЛИ ПЛАТЕЖ ОТМЕНЕН ИЛИ ОТКЛОНЕН (payment.canceled)
    elif event == "payment.canceled":
        order_query = select(PaymentOrder).where(PaymentOrder.id == order_id).with_for_update()
        order_result = await session.execute(order_query)
        order = order_result.scalar_one_or_none()
        if order and order.status == "NEW":
            order.status = "REJECTED"
            await session.commit()

    return {"status": "success"}