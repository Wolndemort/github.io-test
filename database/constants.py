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
            "type": "lessons",
            "schedule": "В процессе разработки",
            "tariffs": [
                {"count": 8, "price": 3500, "days": 30},
                {"count": 12, "price": 4500, "days": 45},
                {"count": 24, "price": 8000, "days": 90}
            ]
        },
        # НОВАЯ СЕКЦИЯ КИКБОКСИНГА
        "kickboxing": {
            "name": "Кикбоксинг",
            "active": True,
            "type": "lessons",
            "schedule": "В процессе разработки",
            "tariffs": [
                {"count": 8, "price": 3800, "days": 30},
                {"count": 12, "price": 4800, "days": 45},
                {"count": 24, "price": 8500, "days": 90}
            ]
        },
        "bjj": {
            "name": "Бразильское джиу-джитсу",
            "active": True,
            "type": "unlimited",
            "schedule": "В процессе разработки",
            "tariffs": [
                {"count": 0, "price": 5000, "days": 30}
            ]
        },
        "yoga": {
            "name": "Йога",
            "active": True,
            "type": "lessons",
            "schedule": "В процессе разработки",
            "tariffs": [
                {"count": 1, "price": 800, "days": 7}
            ]
        }
    },
    "limits": {
        "freeze_days_step": 7,
        "subscription_days": 30
    },
    "turnstile": {
         "enabled": False,  #включение и выключение
         "type": "dingtian_http",   #тип реле (бренд)
         "base_url": "",  #заполняется через админку (фсм)
         "password": "", #заполняется через админку (фсм)
         "relay_index": 1, # первое реле слева
         "pulse_time_seconds": 1, #время откртия
         "timeout_seconds": 5  #таймаут ожидания ответа клуба
    }
}

