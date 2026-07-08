import io
import pandas as pd
from datetime import datetime, date
from typing import List, Dict, Any


def calculate_club_metrics(students_models: List[Any], confirmed_payments: List[Any]) -> Dict[str, Any]:
    """
    Глубокая бизнес-аналитика для роута /revenue.
    Считает РЕАЛЬНУЮ выручку из базы и основные метрики здоровья клуба.
    """
    if not students_models:
        return {"empty": True}

    now = datetime.utcnow()

    # 1. Считаем реальную выручку из платежей (сумма в копейках / 100 = рубли)
    real_revenue = sum(p.amount_kopecks for p in confirmed_payments if p.amount_kopecks) / 100

    data = [
        {
            "balance": s.balance_lessons if s.balance_lessons is not None else 0,
            "is_frozen": getattr(s, "is_frozen", 0) == 1,
            "is_expired": s.expire_date < now if s.expire_date else False
        }
        for s in students_models
    ]
    df = pd.DataFrame(data)
    real_athletes = df[df["balance"] < 500]
    total_count = len(real_athletes)

    if total_count == 0:
        return {"empty": True}

    # Активные: занятия есть, время не вышло, не заморожен
    active_df = real_athletes[
        (real_athletes["balance"] > 0) & (real_athletes["is_expired"] == False) & (real_athletes["is_frozen"] == False)]
    frozen_df = real_athletes[real_athletes["is_frozen"] == True]

    # Закончились: или баланс 0, или дата истекла
    inactive_df = real_athletes[(real_athletes["balance"] == 0) | (real_athletes["is_expired"] == True)]

    retention_rate = round(((len(active_df) + len(frozen_df)) / total_count) * 100, 1) if total_count > 0 else 0

    return {
        "empty": False,
        "total_athletes": total_count,
        "active_passes": len(active_df),
        "frozen_passes": len(frozen_df),
        "inactive_passes": len(inactive_df),
        "retention_rate": retention_rate,
        "revenue": int(real_revenue),
        "total_lessons_left": int(real_athletes["balance"].sum())
    }


def calculate_admin_dashboard(students_models: List[Any]) -> Dict[str, Any]:
    """
    Оперативный пульт для роута /admin (Инструменты администратора).
    """
    if not students_models:
        return {"empty": True}

    now = datetime.utcnow()
    today_date = date.today()

    all_athletes_list = []
    expired_students = []
    burning_students = []
    sleeping_students = []
    birthdays_today = []
    active_now_count = 0

    for s in students_models:
        # Исключаем технические безлимиты
        balance = s.balance_lessons if s.balance_lessons is not None else 0
        if balance >= 500:
            continue

        is_frozen = getattr(s, "is_frozen", 0) == 1
        is_expired = s.expire_date < now if s.expire_date else False

        student_data = {
            "name": s.name or "Атлет",
            "balance": balance,
            "is_frozen": is_frozen,
            "username": getattr(s, "parent", None).full_name if getattr(s, "parent", None) else None,
            # привязка к имени родителя
            "phone": s.parent_phone or ""
        }
        all_athletes_list.append(student_data)

        # 1. Проверяем именинников (сравнение дня и месяца)
        if s.birthday and s.birthday.month == today_date.month and s.birthday.day == today_date.day:
            birthdays_today.append(student_data)

        # 2. Логика статусов
        if is_frozen:
            continue

        if balance > 0 and not is_expired:
            active_now_count += 1
            # Горящие: осталось мало занятий или меньше 5 дней до конца абонемента
            days_left = (s.expire_date - now).days if s.expire_date else 99
            if balance <= 3 or (0 <= days_left <= 5):
                burning_students.append(student_data)

            # Спящие: абонемент активен, но не был в зале больше 14 дней
            if s.last_visit and (now - s.last_visit).days > 14:
                sleeping_students.append(student_data)
        else:
            expired_students.append(student_data)

    return {
        "empty": False,
        "total_athletes": len(all_athletes_list),
        "active_now_count": active_now_count,
        "expired_students": expired_students,
        "burning_students": burning_students,
        "sleeping_students": sleeping_students,
        "birthdays_today": birthdays_today,
        "all_athletes": all_athletes_list
    }


def generate_students_excel(students_models: List[Any]) -> io.BytesIO:
    """
    Генерирует Excel-файл на основе реальной модели Student.
    """
    data = [
        {
            "ФИО Атлета": s.name or "Не указано",
            "Остаток занятий": s.balance_lessons if s.balance_lessons is not None else 0,
            "Статус": "Заморожен" if getattr(s, "is_frozen", 0) == 1 else "Активен",
            "Дата окончания": s.expire_date.strftime("%d.%m.%Y") if s.expire_date else "Не ограничено",
            "Телефон родителя": s.parent_phone or "Не указан",
            "Последний визит": s.last_visit.strftime("%d.%m.%Y %H:%M") if s.last_visit else "Нет визитов"
        }
        for s in students_models
        if (s.balance_lessons if s.balance_lessons is not None else 0) < 500  # убираем безлимиты 999
    ]
    df = pd.DataFrame(data)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Атлеты клуба")

    output.seek(0)
    return output


def calculate_admin_dashboard(students_models: List[Any]) -> Dict[str, Any]:
    """
    Формирует данные для оперативной панели администратора с поддержкой поиска.
    """
    if not students_models:
        return {"empty": True}

    # Собираем данные, включая username в телеграме (подставь свое поле, если оно называется иначе)
    data = [
        {
            "name": s.name or "Атлет",
            "balance": s.balance_lessons if s.balance_lessons is not None else 0,
            "is_frozen": bool(s.is_frozen),
            "username": getattr(s, "username", None) or getattr(s, "tg_username", None)  # ищем юзернейм в модели
        }
        for s in students_models
    ]
    df = pd.DataFrame(data)
    real_athletes = df[df["balance"] < 500]

    if len(real_athletes) == 0:
        return {"empty": True}

    # Списки для обзвона
    expired_list = real_athletes[real_athletes["balance"] == 0].to_dict(orient="records")
    burning_list = real_athletes[(real_athletes["balance"] > 0) & (real_athletes["balance"] <= 3)].to_dict(
        orient="records")

    # Все атлеты для поисковой строки
    all_athletes_list = real_athletes.to_dict(orient="records")

    active_count = real_athletes[(real_athletes["balance"] > 0) & (real_athletes["is_frozen"] == False)].shape

    return {
        "empty": False,
        "total_athletes": len(real_athletes),
        "active_now_count": active_count,
        "expired_students": expired_list,
        "burning_students": burning_list,
        "all_athletes": all_athletes_list  # Отправляем полный список для поиска
    }

