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
        "club_name": "",
        "welcome_text": "Добро пожаловать! Выберите направление:",
        "payment_info": "+79000000000 (Имя Получателя)",  # СБП
        "support_link": "@admin_username",
        "support_enabled": True,
        "site_url": "",
        "site_enabled": False,
        "logo_url": "",
        "loading": {
            "enabled": False,
            "duration_ms": 1200,
            "message": "Загружаем приложение…"
        }
    },

    # 3. ДИСЦИПЛИНЫ (Ключевой блок для хендлеров оплаты)
    "disciplines": {
        "boxing": {
            "name": "Бокс (Дети)",
            "active": False,  # Выключен, пока админ не настроит тарифы
            "type": "lessons",
            "schedule": {"mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []},
            "tariffs": []  # Пусто!
        },
        "kickboxing": {
            "name": "Кикбоксинг",
            "active": False,
            "type": "lessons",
            "schedule": {"mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []},
            "tariffs": []  # Пусто!
        },
        "bjj": {
            "name": "Бразильское джиу-джитсу",
            "active": False,
            "type": "unlimited",
            "schedule": {"mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []},
            "tariffs": []  # Пусто!
        },
        "yoga": {
            "name": "Йога",
            "active": False,
            "type": "lessons",
            "schedule": {"mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []},
            "tariffs": []  # Пусто!
        },
        "grappling": {
            "name": "Грэпплинг",
            "active": False,
            "type": "lessons",
            "schedule": {"mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []},
            "tariffs": []
        },
        "crossfit": {
            "name": "Кроссфит",
            "active": False,
            "type": "lessons",
            "schedule": {"mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []},
            "tariffs": []
        },
        "general": {
            "name": "Общий абонемент",
            "active": False,
            "type": "lessons",
            "scope": "club",
            "schedule": {"mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []},
            "tariffs": []
        }
    },
    "limits": {
        "freeze_days_step": 7,
        "freeze_price_per_day": 0,
        "subscription_days": 30,
        "session_timeout_minutes": 150  # 👈 Добавили таймаут сессии визита в минутах
    },
    "payments": {
        "provider": "yookassa",
        "yookassa_shop_id": "",       # Заполняется админом клуба
        "yookassa_secret_key": "",    # Заполняется админом клуба (live_... или test_...)
        "yookassa_sbp_enabled": True   # Онлайн-СБП через YooKassa
    },
    "turnstile": {
         "enabled": False,  #включение и выключение
         "type": "dingtian_http",   #тип реле (бренд)
         "base_url": "",  #заполняется через админку (фсм)
         "password": "", #заполняется через админку (фсм)
         "relay_index": 0, # дефолт: реле №0, если у платы оно именно так адресуется
         "pulse_time_seconds": 1, #время откртия
         "timeout_seconds": 5,  #таймаут ожидания ответа клуба
         "camera_src": "camera1"  #имя потока из go2rtc.yaml
    }
}

