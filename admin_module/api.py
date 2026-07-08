from handlers.skud import trigger_dingtian_turnstile
from fastapi import Query
from services.analytics import calculate_club_metrics, generate_students_excel, calculate_admin_dashboard
from database.constants import DEFAULT_CLUB_SETTINGS
import hmac
from datetime import datetime, timedelta
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
from config import fastapi_key, T_BANK_SECRET_KEY

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

    # 1. Достаем студентов
    result = await session.execute(select(Student).where(Student.club_id == club_id))
    students = list(result.scalars().all())  # <-- Обернули в list(), теперь PyCharm будет счастлив


    # 2. Достаем конфиг этого конкретного клуба из базы
    # (У тебя наверняка есть функция вроде get_club_config(club_id) или таблица Club)
    # Для примера представим, что ты его откуда-то получаешь:
    club_config = DEFAULT_CLUB_SETTINGS

    # 3. Передаем и студентов, и конфиг в аналитику
    metrics = calculate_club_metrics(students, club_config)

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


# 2. Роут открытия самой страницы WebApp
@router.get("/webapp/live_cam", response_class=HTMLResponse)
async def get_cameras_page(request: Request, club_id: int = Query(...)):
    return templates.TemplateResponse("cameras.html", {"request": request, "club_id": club_id})


# 2. Путь стрима оставляем /webapp/live_cam/stream
# 3. Роут генерации стрима
@router.get("/webapp/live_cam/stream")
async def video_stream(club_id: int = Query(...)):
    """
    Роут транслирует видеопоток в формате MJPEG прямо в WebApp
    """
    # Ссылка на твой локальный контейнер go2rtc внутри сети Docker
    go2rtc_mjpeg_api = "http://gym_go2rtc:1984/api/stream.mjpeg?src=camera1"

    return RedirectResponse(url=go2rtc_mjpeg_api)


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

#PAYMENT PAYMENT




# Используй существующий router из твоего api.py
@router.post("/v1/payments/tbank/webhook")
async def tbank_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    """Прием уведомлений об оплатах от Т-Банка (Т-Кассы)"""
    payload = await request.json()

    # 1. ЗАЩИТА: Проверяем SHA-256 подпись токена, чтобы исключить фейковые запросы
    received_token = payload.get("Token")
    sign_params = payload.copy()

    # Секретный ключ вытаскиваем из переменных окружения
    sign_params["Password"] = T_BANK_SECRET_KEY

    # Эти блоки по регламенту банка удаляются из расчета токена вебхука
    sign_params.pop("Token", None)
    sign_params.pop("Receipt", None)
    sign_params.pop("DATA", None)

    # Сортируем параметры по алфавиту ключей и склеиваем их значения
    sorted_values = [str(sign_params[key]) for key in sorted(sign_params.keys()) if sign_params[key] is not None]
    local_token = hashlib.sha256("".join(sorted_values).encode("utf-8")).hexdigest()

    if local_token != received_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token signature")

    status_payment = payload.get("Status")
    order_id = payload.get("OrderId")

    # 2. Если банк подтвердил успешную оплату (CONFIRMED)
    if status_payment == "CONFIRMED":
        # Ищем исходный заказ в нашей таблице заказов
        order_result = await session.execute(select(PaymentOrder).where(PaymentOrder.id == order_id))
        order = order_result.scalar_one_or_none()

        # Если заказ нашли и он еще не был отмечен как оплаченный
        if order and order.status != "CONFIRMED":
            order.status = "CONFIRMED"

            # Извлекаем RebillId (токен карты для будущих автосписаний)
            rebill_id = payload.get("RebillId")

            # Если это первая оплата (маркер "FIRST") и банк вернул токен карты
            if rebill_id and order.type == "FIRST":
                # Проверяем, нет ли уже созданной подписки на этого студента
                sub_result = await session.execute(
                    select(Subscription).where(
                        Subscription.student_id == order.student_id,
                        Subscription.club_id == order.club_id
                    )
                )
                subscription = sub_result.scalar_one_or_none()

                # Дата следующего списания — ровно через 30 дней
                next_charge = datetime.utcnow() + timedelta(days=30)

                if subscription:
                    # Если запись почему-то уже была — обновляем токен карты и сумму
                    subscription.rebill_id = str(rebill_id)
                    subscription.next_charge_at = next_charge
                    subscription.is_active = True
                    subscription.amount_kopecks = order.amount_kopecks
                else:
                    # Если новая подписка — создаем чистую запись в БД
                    new_sub = Subscription(
                        user_id=order.user_id,
                        student_id=order.student_id,
                        club_id=order.club_id,
                        rebill_id=str(rebill_id),
                        amount_kopecks=order.amount_kopecks,
                        next_charge_at=next_charge,
                        is_active=True
                    )
                    session.add(new_sub)

            # Достаем объект клуба для передачи в add_abon
            club_result = await session.execute(select(Club).where(Club.id == order.club_id))
            club = club_result.scalar_one_or_none()

            # Достаем club_settings из объекта клуба
            club_settings = club.club_settings if club else {}

            # 3. ВЫЗЫВАЕМ ТВОЮ РОДНУЮ ФУНКЦИЮ НАЧИСЛЕНИЯ АБОНЕМЕНТА БЕЗ АДМИНА
            # Она сама обновит баланс, сдвинет expire_date и вернет (new_expire, parent_id)
            abon_result = await add_abon(
                student_id=order.student_id,
                lessons_count=order.lesson_count,  # Берем сохраненное из PaymentOrder
                session=session,
                club_id=order.club_id,
                club_settings=club_settings,
                days_to_add=order.days_to_add  # Берем сохраненное из PaymentOrder
            )

            await session.commit()

            # 4. КРАСИВОЕ SaaS-УВЕДОМЛЕНИЕ ДЛЯ КЛИЕНТА (РОДИТЕЛЯ) ПРЯМО ИЗ ВЕБХУКА
            if abon_result:
                new_expire, parent_id = abon_result
                try:
                    bots_dict = getattr(request.app.state, "bots_dict", {}) # Наш глобальный словарь запущенных ботов клубов

                    bot = bots_dict.get(club.bot_token) if club else None
                    if bot:
                        desc = "БЕЗЛИМИТ" if order.lesson_count == 999 else f"{order.lesson_count} зан."
                        club_name = club_settings.get("ui", {}).get("club_name", club.name)

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
                    print(f"Ошибка отправки уведомления родителю: {e}")

    elif status_payment in ["REJECTED", "CANCELED"]:
        # Если транзакция отклонена банком или отменена пользователем
        order_result = await session.execute(select(PaymentOrder).where(PaymentOrder.id == order_id))
        order = order_result.scalar_one_or_none()
        if order and order.status == "NEW":
            order.status = "REJECTED"
            await session.commit()

    # ВАЖНО: Т-Банк требует строго ответ строкой "OK" со статусом 200
    return "OK"
