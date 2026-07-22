# WebApp / CRM карта проекта

Краткая карта того, что уже сделано и где это лежит.

## Что уже реализовано

- Telegram-бот и старый профиль клиента остаются рабочими параллельно.
- Добавлен WebApp-клиентский кабинет в чёрно-белом премиальном стиле.
- Есть экран атлета, история посещений, история оплат и подписок.
- Есть покупка абонемента, покупка заморозки, бесплатная заморозка.
- Есть привязка профиля по номеру.
- Есть FaceID/QR-переход.
- Есть единая mono-тема для WebApp.
- Есть общий WebApp layout с единым верхним баром.
- Основные WebApp-экраны переведены на общий layout и mono-стили.
- Добавлен безопасный backup retention: локально хранятся только последние 14 архивов.
- Добавлен отдельный `restore-check` для тестовой базы: он проверяет восстановление копии без риска для прода.
- Тестовый restore-check явно отделён от боевого потока бэкапа и описан в README.
- `restore-check` покрыт unit-тестом для CI без реального PostgreSQL.
- Добавлена базовая защита от злоупотреблений: антидубль checkout, lock на привязку и rate limit на спам-клики.
- Добавлен `smoke-check` для post-deploy проверки живого сервиса.
- Telegram `initData` проверяется через HMAC, срок жизни ограничен 24 часами и будущие даты с большим сдвигом отклоняются.
- Redis rate-limit для критичных операций работает fail-closed при ошибке Redis.
- Ручной `/open-turnstile` защищён `X-API-Key`; `/webapp/open-turnstile` проверяет Telegram-подпись и владельца атлета.
- `/ready` возвращает HTTP 503 при проблеме с БД.
- CI/CD после пересборки ждёт `/health` и `/ready`, затем запускает production smoke-check.
- Последняя проверка: 34 теста прошли успешно.
- При отказе турникета транзакция прохода откатывается: `last_visit` и `VisitLog` не фиксируются до успешного ответа реле.
- QR-коды проверяют формат и возраст часового `time_salt`; срок действия — до двух часов.

## Основные маршруты WebApp

- `/webapp/client-cabinet` — главный кабинет клиента.
- `/webapp/client-cabinet/student` — карточка атлета.
- `/admin/students` — клубное управление атлетами для владельца клуба: ДР, баланс, срок абонемента, заморозки и дисциплина.
- `/webapp/client-cabinet/history` — история оплат и подписок.
- `/webapp/client-cabinet/freeze` — бесплатная заморозка.
- `/webapp/client-cabinet/buy-freeze` — покупка заморозки.
- `/webapp/client-cabinet/buy-subscription` — покупка абонемента.
- `/webapp/client-cabinet/auth` — привязка профиля по номеру.
- `/webapp/biometric-pass` — FaceID / QR-переход.
- `/webapp/client-cabinet/buy-freeze` — экран покупки заморозки.
- `/webapp/client-cabinet/freeze` — экран бесплатной заморозки.
- `/webapp/client-cabinet/history` — история оплат и подписок.
- `/webapp/client-cabinet/student` — карточка атлета.

## Бэкапы и тестовое восстановление

- `database/db.py` — создание бэкапа, ротация старых архивов, проверка gzip-архива.
- `scripts/restore_check.py` — отдельный безопасный скрипт для тестового восстановления на тестовой базе.
- `tests/test_restore_check.py` — unit-тесты CI-обёртки restore-check.
- `services/abuse_guard.py` — rate-limit в Redis и audit для заблокированных попыток; критичные операции fail-closed.
- `scripts/smoke_check.py` — post-deploy smoke-check.
- `tests/test_smoke_check.py` — unit-тесты smoke-check обёртки.
- `README.txt` — описание политики бэкапов и запуска restore-check.

## Основные файлы

- `admin_module/api.py` — WebApp-эндпоинты, проверка Telegram initData, создание оплат.
- `handlers/payments.py` — существующая рабочая логика оплат в боте.
- `handlers/user_option.py` — профиль, QR, привязка по номеру, заморозки.
- `handlers/buttons.py` — клавиатуры и WebApp-кнопки.
- `templates/client_cabinet.html` — главный кабинет клиента.
- `templates/webapp_base.html` — общий WebApp layout с верхним баром.
- `templates/webapp_student.html` — карточка атлета.
- `templates/webapp_history.html` — история оплат и подписок.
- `templates/webapp_buy_subscription.html` — покупка абонемента.
- `templates/webapp_bind_phone.html` — привязка по номеру.
- `templates/webapp_freeze.html` — бесплатная заморозка.
- `templates/webapp_buy_freeze.html` — покупка заморозки.
- `templates/biometric_pass.html` — FaceID / QR.
- `templates/schedule.html` — расписание в едином стиле.
- `templates/privacy.html` — политика конфиденциальности.
- `templates/oferta.html` — публичная оферта.
- `templates/cameras.html` — камеры.
- `templates/stats.html` — статистика клуба.
- `static/css/mono.css` — общая чёрно-белая премиальная тема.

## Что важно по логике

- Рабочую механику оплат не переписывали.
- ЮKassa webhook остаётся общим для бота и WebApp.
- Telegram-профиль не удалён.
- WebApp работает параллельно, как отдельный клиентский интерфейс.
- `restore-check` не затрагивает боевую базу и должен запускаться только на отдельной тестовой БД.
- `PROJECT_WEBAPP_MAP.md` в корне — краткая карта по всему проекту и WebApp.

## Контроль безопасности и эксплуатации

- Не передавать пользовательский `initData` между клубами: сервер сверяет подпись токеном конкретного клуба.
- Не использовать `/open-turnstile` из пользовательского WebApp: для него предназначен `/webapp/open-turnstile`.
- При падении Redis платежи и привязка по номеру блокируются до восстановления сервиса.
- После деплоя проверять не только контейнеры, но и результат post-deploy smoke-check.
- При диагностике СКУД проверять, что при сетевой ошибке не появился ложный `VisitLog` и не остался `last_visit`.
- Обычный админ не получает доступ к `/master-dashboard`: его управление идёт через подписанный Telegram WebApp и ограничено `club_id`/`owner_id`.
- Супер-админ сохраняет полный SQLAdmin-доступ к клубам, пользователям, атлетам и истории посещений.

## Production-мониторинг Sentry

Sentry подключён как внешний мониторинг ошибок FastAPI-приложения.

Схема подключения:

1. Пакет `sentry-sdk[fastapi]` добавлен в `requirements.txt`.
2. DSN не хранится в Git и не записывается в код.
3. На production-сервере DSN задаётся в `.env`:

```env
SENTRY_DSN=https://...ingest.de.sentry.io/...
SENTRY_ENVIRONMENT=production
```

4. `main.py` читает `SENTRY_DSN` через `os.getenv` и инициализирует Sentry до запуска FastAPI-приложения.
5. Включён только Error Monitoring; tracing отключён (`SENTRY_TRACES_SAMPLE_RATE=0.0` по умолчанию).
6. Перед отправкой события фильтруются query-параметры и чувствительные заголовки:
   - Telegram `init_data`;
   - cookies;
   - `Authorization`;
   - `X-API-Key`.
7. Ошибки отображаются в проекте Sentry и отправляются на подтверждённый основной email аккаунта согласно настройкам Issue Alerts.

Проверка после деплоя:

```bash
docker compose exec gym-api sh -c 'test -n "$SENTRY_DSN" && echo SENTRY_CONFIGURED || echo SENTRY_MISSING'
```

Если приложение работает без исключений, проект Sentry может оставаться пустым — это нормальное состояние. DSN следует менять в Sentry и на сервере, если он был опубликован за пределами защищённого `.env`.

## Что можно делать дальше

- точечно перевести оставшиеся служебные страницы (`privacy`, `oferta`, `stats`, `cameras`) на общий визуальный каркас;
- уплотнить карточки и навигацию на служебных WebApp-экранах;
- добавить ещё более дорогую микроанимацию и состояния;
- довести клиентский кабинет до почти полной замены обычного профиля без удаления Telegram-версии.
