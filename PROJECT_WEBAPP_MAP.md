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

## Основные маршруты WebApp

- `/webapp/client-cabinet` — главный кабинет клиента.
- `/webapp/client-cabinet/student` — карточка атлета.
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
- `PROJECT_WEBAPP_MAP.md` в корне — краткая карта по всему проекту и WebApp.

## Что можно делать дальше

- точечно перевести оставшиеся служебные страницы (`privacy`, `oferta`, `stats`, `cameras`) на общий визуальный каркас;
- уплотнить карточки и навигацию на служебных WebApp-экранах;
- добавить ещё более дорогую микроанимацию и состояния;
- довести клиентский кабинет до почти полной замены обычного профиля без удаления Telegram-версии.
