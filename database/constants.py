DEFAULT_CLUB_SETTINGS = {
    # 1. Переключатели функционала (То, что в твоем toggle_)
    "features": {
        "freeze": True,  # Заморозка
        "qr_checkin": True,  # Чекин по QR
        "manual_add": True,  # Ручное добавление атлета админом
        "online_payments": False  # Прием платежей через API (ЮKassa и т.д.)
    },

    # 2. Тексты и контакты (Для каждого клуба свои)
    "ui": {
        "club_name": "Новый фитнес-клуб",
        "welcome_text": "Добро пожаловать! Выберите направление:",
        "payment_info": "+79000000000 (Имя Получателя)",  # СБП
        "support_link": "@admin_username"
    },

    # 3. ДИСЦИПЛИНЫ (Ключевой блок для хендлеров оплаты)
    "disciplines": {
        "boxing": {
            "name": "Бокс (Дети)",
            "active": True,
            "type": "lessons",  # Считаем занятия
            "schedule": "1111",
            "tariffs": [
                {"count": 8, "price": 3500, "days": 30},  # Обычный месяц
                {"count": 12, "price": 4500, "days": 45},  # Продленный срок
                {"count": 24, "price": 8000, "days": 90}
            ]
        },
        "bjj": {
            "name": "Бразильское джиу-джитсу",
            "active": True,
            "type": "unlimited",  # Безлимит
            "price": 5000,
            "schedule": "1111"
        },
        "yoga": {
            "name": "Йога",
            "active": False,  # Скроет кнопку у юзеров
            "type": "lessons",
            "tariffs": [{"count": 1, "price": 800}],
            "schedule": "1111"
        }
    },

    # 4. Технические лимиты
    "limits": {
        "freeze_days_step": 7,
        "subscription_days": 30
    }
}
