import pandas as pd
import urllib.request
import base64
import asyncio
from fastapi import Query
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
from database.db import User, Student
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


# 1. Потоковый воркер, который собирает MJPEG из скриншотов камеры
async def stream_worker(snapshot_url: str):
    # Данные авторизации камеры для HTTP-заголовка Basic Auth
    auth_str = "admin:Aemaykop2026"
    auth_b64 = base64.b64encode(auth_str.encode()).decode()

    headers = {
        "Authorization": f"Basic {auth_b64}"
    }

    while True:
        try:
            loop = asyncio.get_event_loop()

            # Скачиваем одиночный snapshot из камеры через пул потоков
            def fetch_image():
                req = urllib.request.Request(snapshot_url, headers=headers)
                with urllib.request.urlopen(req, timeout=2.0) as response:
                    return response.read()

            frame_bytes = await loop.run_in_executor(None, fetch_image)

            # Формируем MJPEG чанк для тега <img> на фронтенде
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

        except Exception as e:
            # Если сеть моргнула или камера занята, мягко ждем 1 секунду
            await asyncio.sleep(1)

        # Ограничитель кадров (~25 FPS)
        await asyncio.sleep(0.04)


# 2. Роут, который генерирует видеопоток для WebApp
@router.get("/webapp/cameras/stream")
async def video_stream(club_id: int = Query(...)):
    # Собираем точную ONVIF HTTP-ссылку для новой прошивки Tiandy
    domain = "camera.aemaykop-skud.netcraze.pro"
    path = "/onvif-http/snapshot?Profile_1"
    snapshot_url = f"http://{domain}{path}"

    return StreamingResponse(
        stream_worker(snapshot_url),
        media_type='multipart/x-mixed-replace; boundary=frame'
    )



