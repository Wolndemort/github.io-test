import httpx
from loguru import logger
from sqlalchemy import func
from handlers.skud import trigger_dingtian_turnstile
from services.gate_control import process_athlete_gate_pass
from fastapi import Query
from services.analytics import generate_students_excel, calculate_admin_dashboard
import hmac
from datetime import datetime, timedelta, timezone
from database.db import PaymentOrder, Subscription
from database.db import add_abon, purchase_student_freeze
import hashlib
from fastapi.responses import StreamingResponse
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
    if API_KEY and header_value and hmac.compare_digest(header_value, API_KEY):
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

    club_res = await session.execute(select(Club).where(Club.id == club_id))
    club = club_res.scalar_one_or_none()

    club_settings = club.club_settings or {} if club else {}
    limits_settings = club_settings.get("limits", {})
    timeout_minutes = limits_settings.get("session_timeout_minutes", 150)

    result = await session.execute(
        select(Student).where(Student.club_id == club_id)
    )
    students = list(result.scalars().all())


    now_local = datetime.now(timezone.utc).replace(tzinfo=None)

    active_sessions = []
    past_sessions = []

    for student in students:
        if student.last_visit:
            last_visit_naive = student.last_visit.replace(tzinfo=None)
            time_passed = now_local - last_visit_naive
            session_end = last_visit_naive + timedelta(minutes=timeout_minutes)

            session_info = {
                "student_id": student.id,
                "name": student.name,
                "balance": student.balance_lessons or 0,
                "last_visit": last_visit_naive.strftime("%d.%m.%Y %H:%M"),
                "session_end": session_end.strftime("%H:%M"),
                "time_passed_mins": int(time_passed.total_seconds() // 60)
            }

            if time_passed < timedelta(minutes=timeout_minutes):
                delta_left = session_end - now_local
                mins_left = int(delta_left.total_seconds() // 60)
                session_info["mins_left"] = max(0, mins_left)
                active_sessions.append(session_info)
            else:
                past_sessions.append(session_info)

    active_sessions.sort(key=lambda x: x["time_passed_mins"])
    past_sessions.sort(key=lambda x: x["time_passed_mins"])
    past_sessions = past_sessions[:20]

    admin_data = calculate_admin_dashboard(students)

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "club_id": club_id,
            "active_sessions": active_sessions,
            "past_sessions": past_sessions,
            "timeout_minutes": timeout_minutes,
            **admin_data
        }
    )


@router.get("/revenue", response_class=HTMLResponse)
async def get_revenue_stats(
        request: Request,
        session: AsyncSession = Depends(get_session)
):
    club_id = get_club_id_from_host(request)

    # 1. Загрузка клуба
    club_res = await session.execute(select(Club).where(Club.id == club_id))
    club = club_res.scalar_one_or_none()
    if club:
        settings = club.club_settings if isinstance(club.club_settings, dict) else {}
        club_name = settings.get("ui", {}).get("club_name") or club.name
    else:
        club_name = "Фитнес-клуб"

    # Настройка честного времени (МСК)

    now_local = datetime.now(timezone.utc).replace(tzinfo=None)

    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now_local.weekday())
    month_start = today_start.replace(day=1)

    # ==========================================
    # БЛОК 1: ФИНАНСЫ (Для твоих графиков/отчетов)
    # ==========================================
    payments_res = await session.execute(
        select(PaymentOrder.amount_kopecks, PaymentOrder.created_at).where(
            PaymentOrder.club_id == club_id,
            PaymentOrder.status == "CONFIRMED",
            PaymentOrder.created_at >= month_start
        )
    )
    all_payments = payments_res.all()

    revenue_today = 0
    revenue_week = 0
    revenue_month = 0

    for row in all_payments:
        amt = row[0]
        dt = row[1]
        p_date = dt.replace(tzinfo=None) if dt else month_start
        amount_rub = (amt or 0) / 100

        revenue_month += amount_rub
        if p_date >= week_start:
            revenue_week += amount_rub
        if p_date >= today_start:
            revenue_today += amount_rub

    # Направления по оплатам
    disc_pay_res = await session.execute(
        select(Student.discipline, func.sum(PaymentOrder.amount_kopecks))
        .join(Student, PaymentOrder.student_id == Student.id)
        .where(PaymentOrder.club_id == club_id, PaymentOrder.status == "CONFIRMED")
        .group_by(Student.discipline)
    )

    # Рекурренты
    type_res = await session.execute(
        select(PaymentOrder.type, func.count(PaymentOrder.id))
        .where(PaymentOrder.club_id == club_id, PaymentOrder.status == "CONFIRMED")
        .group_by(PaymentOrder.type)
    )
    payment_types = {"FIRST": 0, "RECURRENT": 0}
    for row in type_res.all():
        if row[0] in payment_types:
            payment_types[row[0]] = row[1]

    # ==========================================
    # БЛОК 2: АТЛЕТЫ И АБОНЕМЕНТЫ (Для твоего HTML)
    # ==========================================
    # Вытаскиваем ВСЕХ студентов этого клуба одним запросом
    students_res = await session.execute(
        select(Student).where(Student.club_id == club_id)
    )
    students = students_res.scalars().all()

    if not students:
        # Если в клубе пусто — отдаем флаг empty, как просит HTML
        return templates.TemplateResponse(
            "stats.html",
            {"request": request, "empty": True, "club_name": club_name}
        )

    total_athletes = len(students)
    active_passes = 0
    frozen_passes = 0
    burning_passes = 0
    inactive_passes = 0
    total_lessons_left = 0

    churned_students = []
    discipline_counts = {}

    for s in students:
        total_lessons_left += s.balance_lessons

        # Считаем популярность направлений по числу людей
        disc_key = s.discipline or "boxing"
        discipline_counts[disc_key] = discipline_counts.get(disc_key, 0) + 1

        # Распределяем по статусам абонементов
        if s.is_frozen:
            frozen_passes += 1
        elif s.balance_lessons <= 0:
            inactive_passes += 1
            churned_students.append({"name": s.name})
        elif 0 < s.balance_lessons <= 3:
            burning_passes += 1
            active_passes += 1
        else:
            active_passes += 1

    # Красивые имена для дисциплин в HTML
    discipline_names = {
        "boxing": "🥊 Бокс (Дети)",
        "kickboxing": "🤼‍♂️ Кикбоксинг",
        "bjj": "🥋 Бразильское джиу-джитсу",
        "yoga": "🧘‍♂️ Йога"
    }

    disciplines_stats = [
        {"name": discipline_names.get(k, f"🏃‍♂️ {k}"), "active_athletes": v}
        for k, v in discipline_counts.items()
    ]

    # Сортируем топ-атлетов по остатку занятий (первые 5 человек)
    sorted_students = sorted(students, key=lambda x: x.balance_lessons, reverse=True)
    top_students = [{"name": s.name, "balance": s.balance_lessons} for s in sorted_students[:5]]

    # Считаем Retention (Удержание). Например: процент тех, у кого баланс > 0
    retention_rate = round((active_passes / total_athletes) * 100) if total_athletes > 0 else 0

    # 5. ОТДАЕМ ПОЛНЫЙ КОМПЛЕКТ ДАННЫХ В ШАБЛОН
    return templates.TemplateResponse(
        "stats.html",
        {
            "request": request,
            "empty": False,
            "club_id": club_id,
            "club_name": club_name,

            # Данные для HTML-карточек
            "total_athletes": total_athletes,
            "retention_rate": retention_rate,
            "active_passes": active_passes,
            "frozen_passes": frozen_passes,
            "burning_passes": burning_passes,
            "inactive_passes": inactive_passes,
            "total_lessons_left": total_lessons_left,
            "disciplines_stats": disciplines_stats,
            "churned_students": churned_students,
            "top_students": top_students,

            # Финансы (на случай, если захочешь вывести их туда же)
            "revenue_today": round(revenue_today, 2),
            "revenue_week": round(revenue_week, 2),
            "revenue_month": round(revenue_month, 2),
            "payment_types": payment_types
        }
    )


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
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    context = {
        "request": request,
        "club_name": club.name or 'Без названия',
        "disciplines": parsed_disciplines
        ,"loading": {"enabled": bool(loading.get("enabled", False)),
                     "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))),
                     "message": str(loading.get("message", "Загружаем приложение…"))},
        "logo_url": str(ui.get("logo_url", ""))
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


# 2. Роут генерации стрима
@router.get("/webapp/live_cam/stream")
async def video_stream(
        club_id: int = Query(...),
        camera_src: str | None = None,
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
    turnstile_settings = settings.get("turnstile", {})
    if not isinstance(turnstile_settings, dict):
        turnstile_settings = {}
    camera_src = camera_src or turnstile_settings.get("camera_src") or "camera1"

    # Смартфон не видит внутреннюю Docker-сеть. Поэтому FastAPI сам подключается
    # к go2rtc и передает полученные байты наружу без изменения.
    go2rtc_mjpeg_api = "http://host.docker.internal:1984/api/stream.mjpeg"
    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    client = httpx.AsyncClient(timeout=timeout)

    try:
        request = client.build_request(
            "GET",
            go2rtc_mjpeg_api,
            params={"src": camera_src}
        )
        response = await client.send(request, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        logger.error(
            "Не удалось подключиться к go2rtc: club_id={}, camera_src={}, error={}",
            club_id,
            camera_src,
            exc
        )
        raise HTTPException(
            status_code=502,
            detail="Сервис камер временно недоступен"
        ) from exc

    if response.status_code != 200:
        status_code = response.status_code
        await response.aclose()
        await client.aclose()
        logger.error(
            "go2rtc не отдал MJPEG-поток: club_id={}, camera_src={}, status={}",
            club_id,
            camera_src,
            status_code
        )
        raise HTTPException(
            status_code=502,
            detail=f"Камера не отдала видеопоток (go2rtc: {status_code})"
        )

    # В MJPEG заголовок Content-Type содержит boundary — имя разделителя кадров.
    # Нельзя подставлять его вручную: у разных источников оно может отличаться.
    content_type = response.headers.get(
        "content-type",
        "multipart/x-mixed-replace; boundary=frame"
    )

    if "multipart/x-mixed-replace" not in content_type.lower():
        await response.aclose()
        await client.aclose()
        logger.error(
            "go2rtc вернул неожиданный Content-Type: club_id={}, camera_src={}, content_type={}",
            club_id,
            camera_src,
            content_type
        )
        raise HTTPException(
            status_code=502,
            detail="Камера доступна, но не отдает MJPEG. Проверьте кодек в go2rtc"
        )

    async def stream_generator():
        try:
            # aiter_raw сохраняет multipart-разметку и границы JPEG-кадров как есть.
            async for chunk in response.aiter_raw():
                yield chunk
        except httpx.HTTPError as exc:
            logger.warning(
                "MJPEG-поток оборвался: club_id={}, camera_src={}, error={}",
                club_id,
                camera_src,
                exc
            )
        finally:
            await response.aclose()
            await client.aclose()

    # X-Accel-Buffering запрещает Nginx копить кадры в буфере.
    return StreamingResponse(
        stream_generator(),
        headers={
            "Content-Type": content_type,
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no"
        }
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

    club = await db.get(Club, user.club_id)
    settings = (club.club_settings or {}) if club else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    # Достаем список студентов для этого родителя
    students_query = select(Student).where(Student.parent_id == user_id)
    students_result = await db.execute(students_query)
    students = students_result.scalars().all()

    # Рендерим HTML страницу и передаем туда список детей
    return templates.TemplateResponse("biometric_pass.html", {"request": request, "students": students,
        "club_name": club.name if club else "", "logo_url": ui.get("logo_url", ""),
        "loading": {"enabled": bool(loading.get("enabled", False)), "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))), "message": str(loading.get("message", "Загружаем приложение…"))}})



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
        if hmac.compare_digest(calculated_hash, tg_hash):
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
    club = await db.get(Club, club_id)
    settings = (club.club_settings or {}) if club else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}

    # Рендерим HTML и передаем туда список детей
    return templates.TemplateResponse("biometric_pass.html", {"request": request, "students": students,
        "club_name": club.name if club else "", "logo_url": ui.get("logo_url", ""),
        "loading": {"enabled": bool(loading.get("enabled", False)), "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))), "message": str(loading.get("message", "Загружаем приложение…"))}})

# 2. РОУТ ДЛЯ ОБРАБОТКИ НАЖАТИЯ И ОТКРЫТИЯ ТУРНИКЕТА

from services.gate_control import process_athlete_gate_pass


@router.post("/open-turnstile")
async def open_turnstile(payload: dict, db: AsyncSession = Depends(get_session)):
    student_id = payload.get("student_id")

    # 1. Тянем клубные настройки (короткий запрос)
    student_club = await db.execute(select(Student.club_id).where(Student.id == student_id))
    club_id = student_club.scalar()
    club_res = await db.execute(select(Club.club_settings).where(Club.id == club_id))
    club_settings = club_res.scalar() or {}

    # 2. Вызываем наш единый сервис!
    res = await process_athlete_gate_pass(
        student_id, db, club_settings, expected_club_id=club_id
    )

    if not res["success"]:
        return {"success": False, "message": res["message"]}

    final_msg = f"{res['message']} | {res['turnstile_status']}"
    return {"success": True, "message": final_msg}


@router.post("/webapp/open-turnstile")
async def open_webapp_turnstile(
        payload: BiometricCheckIn,
        request: Request,
        db: AsyncSession = Depends(get_session)
):
    """
    Принимает сигнал об успешном FaceID из Telegram WebApp родителя.
    Вызывает центральный сервис СКУД и возвращает статус.
    """
    from database.db import Student, Club, User

    # Базовая валидация (оставляем ее в хендлере, так как она привязана к payload веба)
    student_res = await db.execute(select(Student).where(Student.id == payload.student_id))
    student = student_res.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Студент не найден")

    club_res = await db.execute(select(Club).where(Club.id == student.club_id))
    club = club_res.scalar_one_or_none()
    if not club or not club.bot_token:
        raise HTTPException(status_code=400, detail="Конфигурация клуба не найдена")

    # Валидация init_data из Telegram
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user or "id" not in tg_user:
        raise HTTPException(status_code=403, detail="Ошибка безопасности: Неверные данные WebApp")

    if student.parent_id != tg_user["id"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен: Вы не родитель этого атлета")

    # Вызываем наш центральный сервис прохода!
    club_settings = club.club_settings or {}
    res = await process_athlete_gate_pass(
        payload.student_id, db, club_settings, expected_club_id=club.id
    )

    if not res["success"]:
        return {"success": False, "message": res["message"]}

    # Дополнительно шлем пуш в бота родителю, если сессия новая (не внутри текущей)
    if not res["is_inside_session"] and student.parent_id:
        try:
            bots_dict = getattr(request.app.state, "bots_dict", {})
            bot = bots_dict.get(club.bot_token)
            if bot:
                await bot.send_message(
                    chat_id=int(student.parent_id),
                    text=f"🔔 <b>{club.name}</b>: {res['student_name']} вошел в зал (через WebApp кнопку).",
                    parse_mode="HTML"
                )
        except Exception as e_msg:
            logger.warning(f"Не удалось отправить пуш через WebApp хендлер: {e_msg}")

    # Возвращаем красивый ответ на фронтенд WebApp
    final_text = f"{res['message']}\n{res['turnstile_status']}"
    return {"success": True, "message": final_text}

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
    #auth_header = request.headers.get("Authorization")
    #if not auth_header or not auth_header.startswith("Bearer "):
        #raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid auth header")

    #token = auth_header.split(" ")[1]
    #if token != WEBHOOK_SECRET_TOKEN:
        #raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: Invalid webhook token")

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
            if order.type.startswith("FREEZE"):
                abon_result = await purchase_student_freeze(
                    order.student_id, order.club_id, order.days_to_add, session
                )
            else:
                abon_result = await add_abon(
                    student_id=order.student_id,
                    lessons_count=order.lesson_count,
                    session=session,
                    club_id=order.club_id,
                    club_settings=club_settings,
                    days_to_add=order.days_to_add,
                    discipline=order.discipline
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
                        # === ДОБАВЛЕНО: Догружаем студента из базы для вывода его имени в алерте ===
                        student_res = await session.execute(
                            select(Student).where(Student.id == order.student_id)
                        )
                        student_obj = student_res.scalar_one_or_none()
                        student_name = student_obj.name if student_obj else f"ID {order.student_id}"
                        # =========================================================================

                        desc = (f"заморозка на {order.days_to_add} дн." if order.type.startswith("FREEZE")
                                else ("БЕЗЛИМИТ" if order.lesson_count == 999 else f"{order.lesson_count} зан."))
                        ui_cfg = club_settings.get("ui", {})
                        club_name = ui_cfg.get("club_name", club.name if club else "Фитнес-клуб")

                        # Переводим копейки из базы в рубли для красивого текста
                        amount_rub = (order.amount_kopecks or 0) / 100
                        discipline_raw = getattr(order, 'discipline', 'boxing')

                        discipline_names = {
                            "boxing": "🥊 Бокс",
                            "kickboxing": "🤼‍♂️ Кикбоксинг",
                            "bjj": "🥋 БЖЖ",
                            "yoga": "🧘‍♂️ Йога"
                        }
                        disc_name = ("❄️ Заморозка абонемента" if order.type.startswith("FREEZE")
                                     else discipline_names.get(discipline_raw, f"🏃‍♂️ {discipline_raw}"))
                        card_saved = order.type == "FIRST" and payment_method_id and saved_card_flag
                        client_card_text = (
                            "Карта привязана к системе автопродления. Следующее списание пройдет автоматически."
                            if card_saved else
                            "Оплата успешно зачислена."
                        )

                        # --- А) Сообщение Родителю ---
                        await bot.send_message(
                            chat_id=parent_id,
                            text=f"🥳 <b>Отличные новости!</b>\n\n"
                                 f"Ваша официальная оплата в фитнес-клуб <b>{club_name}</b> успешно получена.\n"
                                 f"Абонемент (<b>{desc}</b>) успешно активирован и действует до: <b>{new_expire}</b>. 🔥\n"
                                 f"{client_card_text}\n\n"
                                 f"<i>Ждем вас на тренировках!</i>",
                            parse_mode="HTML"
                        )

                        # --- Б) Сообщение Владельцу Клуба (Тебе) ---
                        if club.owner_id:
                            await bot.send_message(
                                chat_id=int(club.owner_id),
                                text=f"💰 <b>НОВАЯ ОПЛАТА В СИСТЕМЕ!</b>\n\n"
                                     f"🏰 Клуб: <b>{club_name}</b>\n"
                                # ПРАВКА: Теперь выводим реальное имя, которое догрузили выше
                                     f"👤 Атлет: <b>{student_name}</b>\n"
                                     f"📦 Пакет: <b>{desc}</b>\n"
                                     f"🏷 Направление: <b>{disc_name}</b>\n"
                                     f"💳 Сумма: <code>{amount_rub:,.2f} ₽</code>\n"
                                     f"📅 Действует до: <b>{new_expire}</b>\n\n"
                                     f"📈 <i>Деньги зачислены на баланс, касса клуба обновлена автоматически.</i>",
                                parse_mode="HTML"
                            )

                except Exception as e:
                    logger.error(f"Ошибка отправки сообщения родителю/owner в бот: {e}")
