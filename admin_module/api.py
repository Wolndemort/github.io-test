import pandas as pd
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
# Убедитесь, что импортированы: Club, Student, datetime

@router.get("/webapp/schedule", response_class=HTMLResponse)
async def webapp_schedule_page(
    request: Request,
    session: AsyncSession = Depends(get_session)
):
    # Автоматически вытаскиваем club_id из поддомена (твоя крутая функция!)
    club_id = get_club_id_from_host(request)

    # Тянем настройки клуба, чтобы взять актуальные дисциплины
    stmt = select(Club).where(Club.id == club_id)
    result = await session.execute(stmt)
    club = result.scalar_one_or_none()
    
    if not club:
        return "<h1>🏰 Клуб не найден в системе SpeedyCRM</h1>"

    # Превращаем конфиг в JSON-строку для безопасной передачи прямо в JavaScript фронтенда
    club_settings_json = json.dumps(club.club_settings or {})

    html_content = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Расписание занятий</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            :root {{
                --tg-theme-bg-color: #181818;
                --tg-theme-text-color: #ffffff;
                --tg-theme-hint-color: #aaaaaa;
                --tg-theme-button-color: #2481cc;
                --tg-theme-button-text-color: #ffffff;
            }}
            body {{
                background-color: var(--tg-theme-bg-color, #1c1c1e);
                color: var(--tg-theme-text-color, #ffffff);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                margin: 0;
                padding: 16px;
                -webkit-user-select: none;
            }}
            h2 {{ margin-top: 0; color: var(--tg-theme-button-color); text-align: center; }}
            .discipline-section {{
                margin-bottom: 24px;
            }}
            .discipline-title {{
                font-size: 20px;
                font-weight: bold;
                color: var(--tg-theme-button-color);
                margin-bottom: 12px;
                border-bottom: 2px solid var(--tg-theme-button-color);
                padding-bottom: 4px;
            }}
            .day-container {{
                background: #2c2c2e;
                border-radius: 12px;
                padding: 12px;
                margin-bottom: 12px;
                border: 1px solid #3a3a3c;
            }}
            .day-title {{
                font-size: 16px;
                font-weight: bold;
                margin-bottom: 8px;
                color: #ff9f0a;
            }}
            .lesson-card {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                background: #3a3a3c;
                padding: 10px;
                border-radius: 8px;
                margin-bottom: 8px;
            }}
            .lesson-card:last-child {{ margin-bottom: 0; }}
            .time {{
                font-family: monospace;
                font-size: 16px;
                font-weight: bold;
                background: var(--tg-theme-button-color);
                color: var(--tg-theme-button-text-color);
                padding: 4px 8px;
                border-radius: 6px;
            }}
            .info {{ flex-grow: 1; margin-left: 12px; }}
            .coach {{ font-size: 14px; color: #aeaeb2; }}
            .slots {{ font-size: 12px; color: #30d158; text-align: right; }}
            .no-lessons {{ color: #8e8e93; font-style: italic; font-size: 13px; }}
        </style>
    </head>
    <body>
        <h2>🏰 {{CLUB_NAME}}</h2>
        <div id="schedule-root">Загрузка модулей...</div>

        <script>
            const tg = window.Telegram.WebApp;
            tg.ready();
            tg.expand();

            // Вшиваем настройки клуба напрямую из Python в JavaScript
            const clubSettings = {{CLUB_SETTINGS_JSON}};
            
            const dayNames = {{
                "mon": "Понедельник", "tue": "Вторник", "wed": "Среда",
                "thu": "Четверг", "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье"
            }};

            function renderAllSchedules() {{
                const root = document.getElementById('schedule-root');
                root.innerHTML = ''; 

                const disciplines = clubSettings.disciplines || {{}};
                let activeDisciplinesCount = 0;

                for (const [discKey, discData] of Object.entries(disciplines)) {{
                    // Выводим расписание только для тех секций, которые админ ВКЛЮЧИЛ в системе
                    if (!discData.active) continue;
                    activeDisciplinesCount++;

                    const discSection = document.createElement('div');
                    discSection.className = 'discipline-section';
                    discSection.innerHTML = `<div class="discipline-title">🥋 ${{discData.name}}</div>`;

                    const scheduleData = discData.schedule || {{}};
                    
                    // Если расписание старое (строка) или пустое
                    if (typeof scheduleData === 'string' || !scheduleData) {{
                        discSection.innerHTML += '<div class="day-container"><div class="no-lessons">⏳ Расписание заполняется администратором...</div></div>';
                        root.appendChild(discSection);
                        continue;
                    }}

                    // Пробегаемся по дням недели внутри дисциплины
                    for (const [dayKey, dayName] of Object.entries(dayNames)) {{
                        const lessons = scheduleData[dayKey] || [];
                        if (lessons.length === 0) continue; // Пустые дни скрываем, чтобы не забивать экран

                        const dayDiv = document.createElement('div');
                        dayDiv.className = 'day-container';
                        dayDiv.innerHTML = `<div class="day-title">${{dayName}}</div>`;

                        lessons.forEach(lesson => {{
                            const freeSlots = Math.max(0, lesson.max_slots - lesson.taken_slots);
                            dayDiv.innerHTML += `
                                <div class="lesson-card">
                                    <div class="time">${{lesson.time}}</div>
                                    <div class="info">
                                        <div class="coach">👤 ${{lesson.coach}}</div>
                                    </div>
                                    <div class="slots">
                                        <div>Свободно</div>
                                        <b>${{freeSlots}} / ${{lesson.max_slots}}</b>
                                    </div>
                                </div>
                            `;
                        }});
                        discSection.appendChild(dayDiv);
                    }}

                    // Если админ включил секцию, но вообще ни одного занятия не добавил
                    if (discSection.children.length === 1) {{
                        discSection.innerHTML += '<div class="day-container"><div class="no-lessons">🗓 Тренировок на этой неделе пока нет.</div></div>';
                    }}

                    root.appendChild(discSection);
                }}

                if (activeDisciplinesCount === 0) {{
                    root.innerHTML = '<p class="no-lessons">В клубе пока нет активных спортивных секций.</p>';
                }}
            }}

            // Рендерим расписание из кэша, переданного сервером
            renderAllSchedules();
        </script>
    </body>
    </html>
    """
    
    html_content = html_content.replace("{{CLUB_NAME}}", club.name)
    html_content = html_content.replace("{{CLUB_SETTINGS_JSON}}", club_settings_json)

    return HTMLResponse(content=html_content, status_code=200)




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
