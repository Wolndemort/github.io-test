import io
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_module.router_base import router, templates
from admin_module.utils import get_club_id_from_host, verify_webapp_admin, webapp_auth_gate
from database.db import Club, PaymentOrder, Student, User, get_session
from services.analytics import calculate_admin_dashboard, generate_students_excel


@router.get("/admin", response_class=HTMLResponse)
async def get_admin_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
    init_data: str | None = Query(default=None),
):
    club_id = get_club_id_from_host(request)
    if not init_data:
        return HTMLResponse("""<!doctype html><meta charset='utf-8'>
<script src='https://telegram.org/js/telegram-web-app.js'></script>
<script>
const tg=window.Telegram.WebApp; tg.ready();
if (!tg.initData) document.body.innerText='Откройте админку из Telegram';
else location.replace(location.pathname+'?club_id=' + encodeURIComponent(new URLSearchParams(location.search).get('club_id') || '') + '&init_data=' + encodeURIComponent(tg.initData));
</script>""", status_code=401)
    club = (await session.execute(select(Club).where(Club.id == club_id))).scalar_one_or_none()
    await verify_webapp_admin(club, init_data)
    club_settings = club.club_settings or {} if club else {}
    timeout_minutes = club_settings.get("limits", {}).get("session_timeout_minutes", 150)
    students = list((await session.execute(select(Student).where(Student.club_id == club_id))).scalars().all())
    now_local = datetime.now(timezone.utc).replace(tzinfo=None)
    active_sessions, past_sessions = [], []
    for student in students:
        if student.last_visit:
            last_visit_naive = student.last_visit.replace(tzinfo=None)
            time_passed = now_local - last_visit_naive
            session_end = last_visit_naive + timedelta(minutes=timeout_minutes)
            # БД хранит наивные UTC-времена; для админского интерфейса показываем МСК.
            display_last_visit = last_visit_naive + timedelta(hours=3)
            display_session_end = session_end + timedelta(hours=3)
            info = {"student_id": student.id, "name": student.name, "balance": student.balance_lessons or 0, "last_visit": display_last_visit.strftime("%d.%m.%Y %H:%M"), "session_end": display_session_end.strftime("%H:%M"), "time_passed_mins": int(time_passed.total_seconds() // 60)}
            (active_sessions if time_passed < timedelta(minutes=timeout_minutes) else past_sessions).append(info)
            if time_passed < timedelta(minutes=timeout_minutes):
                info["mins_left"] = max(0, int((session_end - now_local).total_seconds() // 60))
    active_sessions.sort(key=lambda x: x["time_passed_mins"])
    past_sessions.sort(key=lambda x: x["time_passed_mins"])
    return templates.TemplateResponse("admin.html", {"request": request, "club_id": club_id, "active_sessions": active_sessions, "past_sessions": past_sessions[:20], "timeout_minutes": timeout_minutes, **calculate_admin_dashboard(students)})


@router.get("/revenue", response_class=HTMLResponse)
async def get_revenue_stats(request: Request, session: AsyncSession = Depends(get_session), init_data: str | None = Query(default=None)):
    club_id = get_club_id_from_host(request)
    club = (await session.execute(select(Club).where(Club.id == club_id))).scalar_one_or_none()
    if not init_data:
        return webapp_auth_gate(request, club_id)
    if isinstance(init_data, str) or init_data is None:
        await verify_webapp_admin(club, init_data)
    club_name = ((club.club_settings or {}).get("ui", {}).get("club_name") if club else None) or (club.name if club else "Фитнес-клуб")
    now_local = datetime.now(timezone.utc).replace(tzinfo=None)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now_local.weekday())
    month_start = today_start.replace(day=1)
    payments = (await session.execute(select(PaymentOrder.amount_kopecks, PaymentOrder.created_at).where(PaymentOrder.club_id == club_id, PaymentOrder.status == "CONFIRMED", PaymentOrder.created_at >= month_start))).all()
    revenue_today = revenue_week = revenue_month = 0
    for amt, dt in payments:
        p_date = dt.replace(tzinfo=None) if dt else month_start
        rub = (amt or 0) / 100
        revenue_month += rub
        if p_date >= week_start: revenue_week += rub
        if p_date >= today_start: revenue_today += rub
    students = (await session.execute(select(Student).where(Student.club_id == club_id))).scalars().all()
    if not students:
        return templates.TemplateResponse("stats.html", {"request": request, "empty": True, "club_name": club_name})
    total_athletes = len(students)
    active_passes = frozen_passes = burning_passes = inactive_passes = total_lessons_left = 0
    churned_students, discipline_counts = [], {}
    for s in students:
        total_lessons_left += s.balance_lessons
        discipline_counts[s.discipline or "boxing"] = discipline_counts.get(s.discipline or "boxing", 0) + 1
        if s.is_frozen: frozen_passes += 1
        elif s.balance_lessons <= 0: inactive_passes += 1; churned_students.append({"name": s.name})
        elif 0 < s.balance_lessons <= 3: burning_passes += 1; active_passes += 1
        else: active_passes += 1
    names = {"boxing": "🥊 Бокс (Дети)", "kickboxing": "🤼‍♂️ Кикбоксинг", "bjj": "🥋 Бразильское джиу-джитсу", "yoga": "🧘‍♂️ Йога"}
    disciplines_stats = [{"name": names.get(k, f"🏃‍♂️ {k}"), "active_athletes": v} for k, v in discipline_counts.items()]
    top_students = [{"name": s.name, "balance": s.balance_lessons} for s in sorted(students, key=lambda x: x.balance_lessons, reverse=True)[:5]]
    retention_rate = round((active_passes / total_athletes) * 100) if total_athletes > 0 else 0
    return templates.TemplateResponse("stats.html", {"request": request, "empty": False, "club_id": club_id, "club_name": club_name, "total_athletes": total_athletes, "retention_rate": retention_rate, "active_passes": active_passes, "frozen_passes": frozen_passes, "burning_passes": burning_passes, "inactive_passes": inactive_passes, "total_lessons_left": total_lessons_left, "disciplines_stats": disciplines_stats, "churned_students": churned_students, "top_students": top_students, "revenue_today": round(revenue_today, 2), "revenue_week": round(revenue_week, 2), "revenue_month": round(revenue_month, 2), "payment_types": {"FIRST": 0, "RECURRENT": 0}})


@router.get("/stats/export/excel")
async def export_students_to_excel(request: Request, session: AsyncSession = Depends(get_session), init_data: str | None = Query(default=None)):
    club_id = get_club_id_from_host(request)
    club = (await session.execute(select(Club).where(Club.id == club_id))).scalar_one_or_none()
    await verify_webapp_admin(club, init_data)
    students = list((await session.execute(select(Student).where(Student.club_id == club_id))).scalars().all())
    if not students:
        return StreamingResponse(io.BytesIO(), media_type="application/vnd.ms-excel")
    excel_file = generate_students_excel(students)
    return StreamingResponse(excel_file, headers={"Content-Disposition": f'attachment; filename="report_club_{club_id}.xlsx"'}, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/privacy", response_class=HTMLResponse)
async def get_privacy_page(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})


@router.get("/oferta", response_class=HTMLResponse)
async def get_oferta_page(request: Request):
    return templates.TemplateResponse("oferta.html", {"request": request})


@router.get("/webapp/schedule", response_class=HTMLResponse)
async def webapp_schedule_page(request: Request, club_id: int = None, session: AsyncSession = Depends(get_session), init_data: str | None = Query(default=None)):
    from sqlalchemy.future import select
    if not club_id:
        club_id = get_club_id_from_host(request)
    if not club_id:
        return HTMLResponse(content="<h1>❌ Ошибка: Не удалось определить ID клуба</h1>", status_code=400)
    club = (await session.execute(select(Club).where(Club.id == club_id))).scalar_one_or_none()
    if not club:
        return HTMLResponse(content="<h1>🏰 Клуб не найден в системе SpeedyCRM</h1>", status_code=404)
    if not init_data:
        return HTMLResponse("""<!doctype html><meta charset='utf-8'>
<script src='https://telegram.org/js/telegram-web-app.js'></script><script>
const tg=window.Telegram.WebApp; tg.ready();
if (!tg.initData) document.body.innerText='Откройте приложение из Telegram';
else location.replace(location.pathname+'?club_id=' + encodeURIComponent(new URLSearchParams(location.search).get('club_id') || '') + '&init_data=' + encodeURIComponent(tg.initData));
</script>""", status_code=401)
    await verify_webapp_admin(club, init_data)
    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    disciplines_data = settings.get("disciplines", {})
    day_names = {"mon": "Понедельник", "tue": "Вторник", "wed": "Среда", "thu": "Четверг", "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье"}
    parsed_disciplines = []
    if isinstance(disciplines_data, dict):
        for _, disc_content in disciplines_data.items():
            if not isinstance(disc_content, dict): continue
            parsed_days = []
            for day_key, day_title in day_names.items():
                lessons = disc_content.get("schedule", {}).get(day_key, [])
                if not lessons or not isinstance(lessons, list): continue
                parsed_lessons = []
                for lesson in lessons:
                    if not isinstance(lesson, dict): continue
                    max_slots = int(lesson.get("max_slots") or lesson.get("slots") or lesson.get("limit") or 50)
                    taken_slots = int(lesson.get("taken_slots") or 0)
                    parsed_lessons.append({"time": str(lesson.get("time", "00:00")), "coach": str(lesson.get("coach", "Инструктор")), "max_slots": max_slots, "free_slots": max(0, max_slots - taken_slots)})
                if parsed_lessons: parsed_days.append({"title": day_title, "lessons": parsed_lessons})
            parsed_disciplines.append({"name": disc_content.get("name", "Спортивная секция"), "days": parsed_days})
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    return templates.TemplateResponse("schedule.html", {"request": request, "club_name": club.name or "Без названия", "disciplines": parsed_disciplines, "loading": {"enabled": bool(loading.get("enabled", False)), "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))), "message": str(loading.get("message", "Загружаем приложение…"))}, "logo_url": str(ui.get("logo_url", ""))})


@router.get("/webapp/live_cam", response_class=HTMLResponse)
async def get_cameras_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return webapp_auth_gate(request, club_id)
    await verify_webapp_admin(club, init_data)
    return templates.TemplateResponse("cameras.html", {"request": request, "club_id": club_id})
