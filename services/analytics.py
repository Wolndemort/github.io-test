import io
import pandas as pd
from typing import List, Any, Dict



def calculate_club_metrics(students_models: List[Any], club_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Принимает список студентов и конфиг конкретного клуба.
    Возвращает метрики, включая разбивку по дисциплинам.
    """
    if not students_models:
        return {"empty": True}

    # Сбор данных. Добавляем поле discipline (подставь свое имя поля из модели Student)
    data = [
        {
            "name": s.name or "Атлет",
            "balance": s.balance_lessons if s.balance_lessons is not None else 0,
            "is_frozen": bool(s.is_frozen),
            "discipline": getattr(s, "discipline", "unknown")  # берем дисциплину атлета
        }
        for s in students_models
    ]
    df = pd.DataFrame(data)

    # Отсекаем технические безлимиты (999+)
    real_athletes = df[df["balance"] < 500]
    total_count = len(real_athletes)

    if total_count == 0:
        return {"empty": True}

    # Базовые выборки
    active_df = real_athletes[(real_athletes["balance"] > 0) & (real_athletes["is_frozen"] == False)]
    frozen_df = real_athletes[(real_athletes["balance"] > 0) & (real_athletes["is_frozen"] == True)]
    inactive_df = real_athletes[real_athletes["balance"] == 0]

    # --- СТАТИСТИКА ПО ДИСЦИПЛИНАМ ---
    disciplines_stats = []
    config_disciplines = club_config.get("disciplines", {})

    # Считаем только АКТИВНЫХ студентов по каждой дисциплине
    active_by_discipline = active_df["discipline"].value_counts()

    for key, info in config_disciplines.items():
        # Получаем количество активных, если никого нет — ставим 0
        count = int(active_by_discipline.get(key, 0))

        # Нам интересны только те дисциплины, которые включены в клубе
        # или где уже есть активные люди (на случай, если дисциплину выключили, а люди остались)
        if info.get("active") or count > 0:
            disciplines_stats.append({
                "key": key,
                "name": info.get("name", key),
                "active_athletes": count
            })

    # Сортируем дисциплины по популярности (где больше людей — те выше)
    disciplines_stats = sorted(disciplines_stats, key=lambda x: x["active_athletes"], reverse=True)

    # Остальные метрики
    burning_pass_count = real_athletes[(real_athletes["balance"] > 0) & (real_athletes["balance"] <= 3)].shape[0]
    retention_rate = round(((len(active_df) + len(frozen_df)) / total_count) * 100, 1)
    churned_students = inactive_df[["name"]].to_dict(orient="records")
    top_students = real_athletes.nlargest(3, "balance")[["name", "balance"]].to_dict(orient="records")

    return {
        "empty": False,
        "total_athletes": total_count,
        "active_passes": len(active_df),
        "frozen_passes": len(frozen_df),
        "inactive_passes": len(inactive_df),
        "burning_passes": burning_pass_count,
        "retention_rate": retention_rate,
        "total_lessons_left": int(real_athletes["balance"].sum()),
        "top_students": top_students,
        "churned_students": churned_students,
        "disciplines_stats": disciplines_stats  # Передаем массив со статистикой по направлениям
    }



def generate_students_excel(students_models: List[Any]) -> io.BytesIO:
    """
    Принимает список студентов конкретного клуба, отсекает тех-аккаунты,
    красиво переименовывает колонки и упаковывает всё в буфер Excel.
    """
    data = [
        {
            "ФИО Атлета": s.name or "Не указано",
            "Остаток занятий": s.balance_lessons if s.balance_lessons is not None else 0,
            "Статус заморозки": "Заморожен" if s.is_frozen else "Активен"
        }
        for s in students_models
        if (s.balance_lessons if s.balance_lessons is not None else 0) < 500  # отсекаем 999+ безлимиты
    ]
    df = pd.DataFrame(data)

    output = io.BytesIO()
    # openpyxl обычно работает стабильнее в связке с FastAPI, чем xlsxwriter
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

