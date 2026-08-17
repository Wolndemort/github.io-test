import io
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_module.router_base import router, templates
from admin_module.utils import get_club_id_from_host, verify_webapp_admin, webapp_auth_gate
from admin_module.webapp_verify import verify_telegram_data
from database.db import CartOrder, CashEntry, Club, PaymentOrder, Student, User, VisitLog, get_session
from services.analytics import calculate_admin_dashboard, calculate_cash_flow_periods, calculate_revenue_periods, calculate_student_metrics, generate_students_excel, reporting_periods, moscow_date_boundary


@router.get("/admin", response_class=HTMLResponse)
async def get_admin_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
    init_data: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
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
    students_by_id = {student.id: student for student in students}
    visit_logs = list((await session.execute(
        select(VisitLog).where(VisitLog.club_id == club_id).order_by(VisitLog.visited_at.desc())
    )).scalars().all())
    now_local = reporting_periods()["now"]
    start_date = moscow_date_boundary(date_from).date() if date_from else None
    end_date = moscow_date_boundary(date_to).date() if date_to else None
    active_sessions, past_sessions = [], []
    for student in students:
        if student.last_visit:
            last_visit_naive = student.last_visit.replace(tzinfo=None)
            time_passed = now_local - last_visit_naive
            session_end = last_visit_naive + timedelta(minutes=timeout_minutes)
            # БД хранит наивные UTC-времена; для админского интерфейса показываем МСК.
            display_last_visit = last_visit_naive + timedelta(hours=3)
            display_session_end = session_end + timedelta(hours=3)
            display_date = display_last_visit.date()
            if start_date and display_date < start_date:
                continue
            if end_date and display_date > end_date:
                continue
            info = {"student_id": student.id, "name": student.name, "balance": student.balance_lessons or 0, "parent_id": student.parent_id, "last_visit": display_last_visit.strftime("%d.%m.%Y %H:%M"), "session_end": display_session_end.strftime("%H:%M"), "time_passed_mins": int(time_passed.total_seconds() // 60)}
            (active_sessions if time_passed < timedelta(minutes=timeout_minutes) else past_sessions).append(info)
            if time_passed < timedelta(minutes=timeout_minutes):
                info["mins_left"] = max(0, int((session_end - now_local).total_seconds() // 60))
    # История должна строиться по журналу СКУД, а не по последнему визиту
    # ученика: last_visit хранит только одну (последнюю) отметку.
    past_sessions = []
    for visit in visit_logs:
        student = students_by_id.get(visit.student_id)
        if not student or not visit.visited_at:
            continue
        visit_at = visit.visited_at.replace(tzinfo=None)
        display_visit_at = visit_at + timedelta(hours=3)
        if start_date and display_visit_at.date() < start_date:
            continue
        if end_date and display_visit_at.date() > end_date:
            continue
        elapsed = now_local - visit_at
        if elapsed < timedelta(minutes=timeout_minutes):
            continue
        past_sessions.append({
            "student_id": student.id,
            "name": student.name,
            "balance": student.balance_lessons or 0,
            "parent_id": student.parent_id,
            "last_visit": display_visit_at.strftime("%d.%m.%Y %H:%M"),
            "session_end": (visit_at + timedelta(minutes=timeout_minutes, hours=3)).strftime("%H:%M"),
            "time_passed_mins": int(elapsed.total_seconds() // 60),
        })
    active_sessions.sort(key=lambda x: x["time_passed_mins"])
    past_sessions.sort(key=lambda x: x["last_visit"], reverse=True)
    return templates.TemplateResponse("admin.html", {"request": request, "club_id": club_id, "active_sessions": active_sessions, "past_sessions": past_sessions, "timeout_minutes": timeout_minutes, "filters": {"date_from": date_from or "", "date_to": date_to or ""}, **calculate_admin_dashboard(students, visit_logs=visit_logs)})


@router.get("/revenue", response_class=HTMLResponse)
async def get_revenue_stats(request: Request, session: AsyncSession = Depends(get_session), init_data: str | None = Query(default=None), date_from: str | None = Query(default=None), date_to: str | None = Query(default=None)):
    club_id = get_club_id_from_host(request)
    club = (await session.execute(select(Club).where(Club.id == club_id))).scalar_one_or_none()
    if not init_data:
        return webapp_auth_gate(request, club_id)
    if isinstance(init_data, str) or init_data is None:
        await verify_webapp_admin(club, init_data)
    club_name = ((club.club_settings or {}).get("ui", {}).get("club_name") if club else None) or (club.name if club else "Фитнес-клуб")
    periods = reporting_periods()
    start = moscow_date_boundary(date_from) if date_from else periods["month"]
    end = moscow_date_boundary(date_to) + timedelta(days=1) if date_to else None
    payment_filter = [PaymentOrder.club_id == club_id, PaymentOrder.status == "CONFIRMED", PaymentOrder.created_at >= start]
    cart_filter = [CartOrder.club_id == club_id, CartOrder.status == "CONFIRMED", CartOrder.created_at >= start]
    cash_filter = [CashEntry.club_id == club_id, CashEntry.created_at >= start]
    if end:
        payment_filter.append(PaymentOrder.created_at < end); cart_filter.append(CartOrder.created_at < end); cash_filter.append(CashEntry.created_at < end)
    payments = (await session.execute(select(PaymentOrder.amount_kopecks, PaymentOrder.created_at).where(*payment_filter))).all()
    cart_payments = (await session.execute(select(CartOrder.amount_kopecks, CartOrder.created_at).where(*cart_filter))).all()
    cash_entries = (await session.execute(select(CashEntry).where(*cash_filter))).scalars().all()
    rows = [type("PaymentRow", (), {"amount_kopecks": amount, "created_at": created_at}) for amount, created_at in payments]
    rows.extend(type("PaymentRow", (), {"amount_kopecks": amount, "created_at": created_at}) for amount, created_at in cart_payments)
    revenue = calculate_revenue_periods(rows)
    revenue_today, revenue_week, revenue_month = revenue["today"], revenue["week"], revenue["month"]
    cash_flow = calculate_cash_flow_periods(cash_entries, now=periods["now"])
    students = (await session.execute(select(Student).where(Student.club_id == club_id))).scalars().all()
    if not students:
        return templates.TemplateResponse("stats.html", {"request": request, "empty": True, "club_name": club_name})
    metrics = calculate_student_metrics(students, now=periods["now"])
    total_athletes = metrics["total_athletes"]
    total_parents = metrics["total_parents"]
    active_passes = metrics["active_passes"]
    frozen_passes = metrics["frozen_passes"]
    burning_passes = metrics["burning_passes"]
    inactive_passes = metrics["inactive_passes"]
    total_lessons_left = metrics["total_lessons_left"]
    churned_students = [{"name": s.name, "parent_id": getattr(s, "parent_id", None)} for s in metrics["inactive_students"]]
    discipline_counts = metrics["discipline_counts"]
    names = {"boxing": "🥊 Бокс", "kickboxing": "🤼‍♂️ Кикбоксинг", "bjj": "🥋 Бразильское джиу-джитсу", "yoga": "🧘‍♂️ Йога"}
    disciplines_stats = [{"name": names.get(k, f"🏃‍♂️ {k}"), "active_athletes": v} for k, v in discipline_counts.items()]
    top_students = [{"name": s.name, "balance": s.balance_lessons or 0, "parent_id": getattr(s, "parent_id", None)} for s in sorted(students, key=lambda x: x.balance_lessons or 0, reverse=True)[:5]]
    return templates.TemplateResponse("stats.html", {"request": request, "empty": False, "club_id": club_id, "club_name": club_name, "filters": {"date_from": date_from or "", "date_to": date_to or ""}, "total_athletes": total_athletes, "total_parents": total_parents, "retention_rate": metrics["retention_rate"], "active_passes": active_passes, "frozen_passes": frozen_passes, "burning_passes": burning_passes, "inactive_passes": inactive_passes, "total_lessons_left": total_lessons_left, "disciplines_stats": disciplines_stats, "churned_students": churned_students, "top_students": top_students, "revenue_today": round(revenue_today, 2), "revenue_week": round(revenue_week, 2), "revenue_month": round(revenue_month, 2), "expenses_today": cash_flow["today_expense"], "expenses_week": cash_flow["week_expense"], "expenses_month": cash_flow["month_expense"], "cash_income_today": cash_flow["today_income"], "cash_income_week": cash_flow["week_income"], "cash_income_month": cash_flow["month_income"], "cash_margin_today": cash_flow["today_margin"], "cash_margin_week": cash_flow["week_margin"], "cash_margin_month": cash_flow["month_margin"], "payment_types": {"FIRST": 0, "RECURRENT": 0}})


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
    if not verify_telegram_data(init_data, club.bot_token):
        raise HTTPException(status_code=403, detail="Недействительные данные Telegram")
    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    disciplines_data = settings.get("disciplines", {})
    day_names = {"mon": "Понедельник", "tue": "Вторник", "wed": "Среда", "thu": "Четверг", "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье"}
    parsed_disciplines = []
    if isinstance(disciplines_data, dict):
        for _, disc_content in disciplines_data.items():
            if not isinstance(disc_content, dict): continue
            if not disc_content.get("active", True): continue
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
                if parsed_lessons: parsed_days.append({"key": day_key, "title": day_title, "lessons": parsed_lessons})
            parsed_disciplines.append({"name": disc_content.get("name", "Спортивная секция"), "days": parsed_days})
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    loading_logo = str(loading.get("logo_url") or ui.get("logo_url") or "").strip()
    if loading_logo and loading.get("logo_rev"):
        loading_logo = f"{loading_logo}?v={loading['logo_rev']}"
    return templates.TemplateResponse("schedule.html", {"request": request, "club_name": club.name or "Без названия", "disciplines": parsed_disciplines, "loading": {"enabled": bool(loading.get("enabled", False)), "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))), "message": str(loading.get("message", "Загружаем приложение…"))}, "logo_url": loading_logo})


@router.get("/webapp/live_cam", response_class=HTMLResponse)
async def get_cameras_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return webapp_auth_gate(request, club_id)
    await verify_webapp_admin(club, init_data)
    return templates.TemplateResponse("cameras.html", {"request": request, "club_id": club_id})
