from datetime import datetime
from handlers.skud import trigger_dingtian_turnstile
import pandas as pd
import asyncio
from fastapi.responses import RedirectResponse
from fastapi import Query
import hmac
import httpx
import hashlib
from loguru import logger
import json
from urllib.parse import parse_qsl
import io
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Depends, HTTPException, APIRouter, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette import status
from starlette.responses import StreamingResponse
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
        request: Request,  # <--- ИСПРАВИЛИ ОПЕЧАТКУ ЗДЕСЬ!
        session: AsyncSession = Depends(get_session)
):
    club_id = get_club_id_from_host(request)

    result = await session.execute(
        select(Student).where(Student.club_id == club_id)
    )
    students = result.scalars().all()

    return templates.TemplateResponse(
        "stats.html",  # Временно отдаем stats.html, пока не сверстаешь полноценную админку
        {"request": request, "club_id": club_id, "students": students}
    )


#
# 2. Роут /revenue (убрали get_api_key, чтобы открывался в WebApp Телеграма)
@router.get("/revenue", response_class=HTMLResponse)
async def get_revenue_stats(
        request: Request,
        session: AsyncSession = Depends(get_session)
):
    club_id = get_club_id_from_host(request)

    # ИЗОЛЯЦИЯ SAAS: Вытаскиваем студентов ТОЛЬКО этого конкретного клуба!
    result = await session.execute(
        select(Student).where(Student.club_id == club_id)
    )
    students = result.scalars().all()
    if not students:
        return HTMLResponse(content="<h1>Данных пока нет</h1>", status_code=200)

    # Безопасный сбор данных с защитой от None
    data = [
        {
            "name": s.name or "Атлет",
            "balance": s.balance_lessons if s.balance_lessons is not None else 0,
            'is_frozen': s.is_frozen if s.is_frozen is not None else 0
        }
        for s in students
    ]
    df = pd.DataFrame(data)

    # 1. Общий баланс занятий для турникета считаем по ВСЕМ в этом клубе
    total_lessons = df["balance"].sum()

    # 2. ФИКС ФИНАНСОВ: Для выручки отсекаем технический баланс 999
    real_packages = df[df["balance"] < 500]

    # Считаем реальную выручку на основе проданных пакетов занятий
    estimated_revenue = real_packages["balance"].sum() * 500

    frozen_count = df[df["is_frozen"] == 1].shape[0]

    # Для Топа атлетов тоже отсекаем 999, чтобы там висели реальные люди
    real_students_df = df[df["balance"] < 500]
    top_students = real_students_df.nlargest(3, "balance")[["name", "balance"]].to_dict(orient="records")

    return templates.TemplateResponse(
        "stats.html",
        {"request": request,
         "club_id": club_id,  # Передаем club_id в HTML, чтобы вывести на экран
         "total_lessons": int(total_lessons),
         "revenue": int(estimated_revenue),
         "frozen": frozen_count,
         "top_students": top_students}
    )


# 3. Выгрузка в Excel. Перенесли префикс /stats/export/excel прямо в декоратор
@router.get("/stats/export/excel")
async def export_students_to_excel(request: Request, session: AsyncSession = Depends(get_session)):
    club_id = get_club_id_from_host(request)

    result = await session.execute(select(Student))
    df = pd.DataFrame([{"Имя": s.name, "Баланс": s.balance_lessons} for s in result.scalars().all()])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Атлеты')
    output.seek(0)
    headers = {'Content-Disposition': f'attachment; filename="report_club_{club_id}.xlsx"'}
    return StreamingResponse(output, headers=headers, media_type='application/vnd.ms-excel')



from fastapi.responses import HTMLResponse


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


#CAMERAS CAMERAS CAMERAS
# 1. Потоковый воркер, который автоматически найдет рабочий URL и соберет MJPEG
async def stream_worker(snapshot_urls: list):
    # Используем встроенный в httpx Digest-клиент для авторизации на камерах Tiandy
    auth = httpx.DigestAuth("admin", "Aemaykop2026")

    working_url = None

    # Создаем асинхронный клиент для проверки путей
    async with httpx.AsyncClient(auth=auth, timeout=3.0) as client:
        for url in snapshot_urls:
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    working_url = url
                    logger.success(f"🎥 Успешное внешнее подключение к Tiandy по URL: {url}")
                    break
                else:
                    logger.warning(f"⚠️ Камера вернула статус {response.status_code} для пути: {url}")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка проверки внешнего пути {url}: {e}")
                continue

    if not working_url:
        logger.error("❌ Ни один внешний URL KeenDNS не ответил. Проверьте проброс портов в Keenetic.")
        # Если всё упало, берем первый как запасной
        working_url = snapshot_urls[0]

    # Бесконечный цикл трансляции кадров через HTTPX Digest
    async with httpx.AsyncClient(auth=auth, timeout=3.0) as client:
        while True:
            try:
                response = await client.get(working_url)
                if response.status_code == 200:
                    frame_bytes = response.content
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                else:
                    await asyncio.sleep(1)
            except Exception as frame_err:
                logger.error(f"❌ Ошибка фонового кадра: {frame_err}")
                await asyncio.sleep(1)

            await asyncio.sleep(0.04)


# 2. Роут открытия самой страницы WebApp
@router.get("/webapp/live_cam", response_class=HTMLResponse)
async def get_cameras_page(request: Request, club_id: int = Query(...)):
    return templates.TemplateResponse("cameras.html", {"request": request, "club_id": club_id})



# 3. Роут генерации стрима

import httpx
from fastapi.responses import StreamingResponse


# 2. Путь стрима оставляем /webapp/live_cam/stream
@router.get("/webapp/live_cam/stream")
async def video_stream(club_id: int = Query(...)):
    camera_domain = "camera.aemaykop-skud.netcraze.pro"
    rtsp_url = f"http://admin:Aemaykop2026@{camera_domain}/asapi/v1/video/channels/1/stream"

    # Стучимся к соседу по контейнеру go2rtc
    go2rtc_url = f"http://go2rtc:1984/api/stream.mjpeg?src={rtsp_url}"

    async def stream_generator():
        timeout = httpx.Timeout(None)
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            try:
                async with client.stream("GET", go2rtc_url) as response:
                    async for chunk in response.aiter_bytes():
                        yield chunk
            except Exception as e:
                logger.error(f"❌ Ошибка трансляции потока внутри FastAPI: {e}")

    return StreamingResponse(
        stream_generator(),
        media_type='multipart/x-mixed-replace; boundary=frame'
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


@router.post("/open-turnstile")
async def open_turnstile(payload: dict, db: AsyncSession = Depends(get_session)):
    """
    Сюда летит POST-запрос с фронтенда после успешного сканирования FaceID
    """
    student_id = payload.get("student_id")
    biometric_token = payload.get("biometric_token")
    init_data = payload.get("init_data")

    # 1. Сюда добавляем валидацию init_data (чтобы проверить, что запрос не подделан)
    # 2. Проверяем баланс занятий студента (student.balance_lessons)
    # 3. Отправляем команду на реле Dingtian

    return {"success": True, "message": "Реле сработало, турникет открыт"}



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


@router.post("/webapp/open-turnstile")
async def open_turnstile(payload: BiometricCheckIn, db: AsyncSession = Depends(get_session)):
    """Принимает сигнал об успешном FaceID, проверяет лимиты и дергает реле"""
    student_query = select(Student).where(Student.id == payload.student_id)
    student_res = await db.execute(student_query)
    student = student_res.scalar_one_or_none()

    if not student:
        raise HTTPException(status_code=404, detail="Студент не найден")

    club_query = select(Club).where(Club.id == student.club_id)
    club_res = await db.execute(club_query)
    club = club_res.scalar_one_or_none()

    if not club or not club.bot_token:
        raise HTTPException(status_code=400, detail="Конфигурация клуба не найдена")

    # Безопасность Telegram
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user or "id" not in tg_user:
        raise HTTPException(status_code=403, detail="Ошибка безопасности: Неверные данные")

    telegram_user_id = tg_user["id"]

    if student.parent_id != telegram_user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещен: Вы не родитель")

    user_query = select(User).where(User.user_id == telegram_user_id)
    user_res = await db.execute(user_query)
    parent_user = user_res.scalar_one_or_none()

    # Защита от обхода FaceID
    if parent_user and getattr(parent_user, 'is_biometric_enabled', False):
        if not payload.biometric_token:
            raise HTTPException(status_code=400, detail="Необходимо биометрическое подтверждение")

    # Проверки абонемента
    if student.is_frozen == 1:
        return {"success": False, "message": "Абонемент заморожен."}
    if student.expire_date and student.expire_date < datetime.now():
        return {"success": False, "message": "Срок действия абонемента истек."}
    if student.balance_lessons <= 0:
        return {"success": False, "message": "Нет доступных занятий."}

    # Открываем СКУД Dingtian
    raw_turnstile = club.club_settings.get("turnstile", {})
    relay_config = dict(raw_turnstile) if raw_turnstile else {}

    # 2. Проверяем адрес и принудительно добавляем http:// если его забыли
    if "base_url" in relay_config:
        url_val = str(relay_config["base_url"])
        if not url_val.startswith("http"):
            relay_config["base_url"] = f"http://{url_val}"

    # 3. Отправляем команду на открытие реле
    try:
        is_opened = await trigger_dingtian_turnstile(relay_config)
    except Exception as e:
        return {"success": False, "message": f"Ошибка СКУД: {str(e)}"}


    if is_opened:
        student.balance_lessons -= 1
        student.last_visit = datetime.now()
        await db.commit()
        return {"success": True, "message": f"Турникет открыт для {student.name}!"}

    return {"success": False, "message": "Турникет не ответил. Попробуйте еще раз."}


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
