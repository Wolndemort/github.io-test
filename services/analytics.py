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


def calculate_daily_business_report(students_models: List[Any], today_payments: List[Any],
                                    yesterday_payments: List[Any]) -> dict:
    """
    Вычисляет расширенную бизнес-статистику за день,
    сравнивает показатели со вчерашним днем и анализирует пиковые часы.
    """
    # 1. Расчет выручки
    revenue_today = sum(p.amount_kopecks for p in today_payments if p.amount_kopecks) / 100
    revenue_yesterday = sum(p.amount_kopecks for p in yesterday_payments if p.amount_kopecks) / 100

    revenue_diff = revenue_today - revenue_yesterday
    if revenue_yesterday > 0:
        revenue_percent = round((revenue_diff / revenue_yesterday) * 100, 1)
    else:
        revenue_percent = 100.0 if revenue_today > 0 else 0.0

    # Знаки для красивого вывода в ТГ
    rev_sign = "📈 +" if revenue_diff >= 0 else "📉 "

    # 2. Анализ студентов через Pandas
    if not students_models:
        return {
            "revenue_today": int(revenue_today),
            "revenue_diff_text": f"{rev_sign}{int(revenue_diff)} ₽ ({revenue_percent}%)",
            "top_discipline": "Нет данных",
            "peak_hours": "Нет данных",
            "total_athletes": 0
        }

    data = [
        {
            "balance": s.balance_lessons if s.balance_lessons is not None else 0,
            "discipline": getattr(s, "discipline", "unknown"),
            "last_visit": s.last_visit
        }
        for s in students_models
    ]
    df = pd.DataFrame(data)
    real_athletes = df[df["balance"] < 500]

    # 3. Самая популярная дисциплина среди ВСЕХ клиентов клуба
    if not real_athletes.empty and "discipline" in real_athletes.columns:
        top_disc_series = real_athletes["discipline"].value_counts()
        if not top_disc_series.empty:
            # Берем ключ самой популярной дисциплины
            top_discipline_key = top_disc_series.index[0]
            top_discipline = str(top_discipline_key).upper()
        else:
            top_discipline = "НЕ ОПРЕДЕЛЕНА"
    else:
        top_discipline = "НЕТ АТЛЕТОВ"

    # 4. Пиковые часы посещений на основе поля last_visit
    # Смотрим на визиты, которые были СЕГОДНЯ
    today_date = datetime.utcnow().date()

    # Безопасно фильтруем строки, где есть дата визита и она сегодняшняя
    today_visits = real_athletes[
        (real_athletes["last_visit"].notna()) &
        (real_athletes["last_visit"].apply(lambda x: x.date() == today_date if hasattr(x, 'date') else False))
        ]

    if not today_visits.empty:
        # Извлекаем час визита
        today_visits["hour"] = today_visits["last_visit"].apply(lambda x: x.hour)
        hour_counts = today_visits["hour"].value_counts()

        if not hour_counts.empty:
            # Берем топ-2 самых популярных часа для посещения
            peaks = hour_counts.head(2).index.tolist()
            # Форматируем красиво, например: "18:00, 19:00"
            peak_hours = ", ".join([f"{h}:00" for h in sorted(peaks)])
        else:
            peak_hours = "Равномерно"
    else:
        peak_hours = "Нет чекинов сегодня"

    return {
        "revenue_today": int(revenue_today),
        "revenue_diff_text": f"{rev_sign}{int(revenue_diff)} ₽ ({revenue_percent}%)",
        "top_discipline": top_discipline,
        "peak_hours": peak_hours,
        "total_athletes": len(real_athletes)
    }
