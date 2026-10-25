# SpeedyCRM — актуальный статус проекта

Дата актуализации: 04.09.2026

## Клиентская документация

Основной понятный документ для владельцев клубов и сотрудников: [CLIENT_SUPPORT_PRESENTATION.md](CLIENT_SUPPORT_PRESENTATION.md).

Полный индекс документов: [DOCUMENTATION_INDEX.md](DOCUMENTATION_INDEX.md).

Последняя проверка сценариев тарифов и дисциплин: 64 профильных теста пройдены; компиляция и `git diff --check` пройдены. Полный набор содержит 534 успешных теста и 3 известных падения в незавершённой modern web-ветке; эти падения не относятся к Telegram и legacy WebApp-сценариям тарифов.

## Рабочая область

- Репозиторий: `C:\Users\79615\PycharmProjects\aaaa`
- Текущая ветка: `web-migration/phase-0-auth`
- `master` и `aaaa-production` не изменять и не сливать без отдельного разрешения.
- Web-миграция — отдельная ветка/контейнер. Telegram-функционал сохраняется.
- ALTER — отдельный проект, не изменять.

## Серверы и окружения

Подключение по SSH:

```text
ssh -i C:\Users\79615\.ssh\alter_agent root@77.73.131.175
```

Staging SpeedyCRM:

- каталог: `/root/speedycrm-staging`
- compose: `docker-compose.staging.yml`
- API: `127.0.0.1:18000`
- HTTPS: `https://staging.speedycrm.ru:18443`
- контейнеры: `speedycrm_staging_api`, `speedycrm_staging_db`, `speedycrm_staging_redis`, `speedycrm_staging_nginx`
- проверка: `curl http://127.0.0.1:18000/health` и `/ready`
- staging можно пересобрать только после локальных тестов:

```text
cd /root/speedycrm-staging
docker compose -f docker-compose.staging.yml up -d --build
```

Production SpeedyCRM:

- каталог: `/root/github.io-test`
- production не обновлять из web-ветки.

ALTER:

- локальный проект: `C:\Users\79615\PycharmProjects\alter`
- рабочий сервер/путь ALTER не менять в рамках SpeedyCRM Web-миграции.

Секреты, токены, пароли и содержимое `.env` в этот файл не записывать.

## Что реализовано

### Общая платформа

- Telegram и native Web используют общую БД, модели и бизнес-сервисы.
- Native email OTP, Redis-сессии, HttpOnly session cookie и CSRF.
- AuthContext, actor/user/club scope, permissions и feature flags.
- Idempotency через Redis для mutations.
- `with_for_update` для критических изменений и защиты от гонок.
- Server-side пересчёт цен, клубная изоляция, audit events, rate limits.
- Rollback-совместимость: Telegram-маршруты не удалены.

### Staff Web

Реализованы разделы открытия турникета, документы, график, check-in, продажи, склад/товары, касса, атлеты, расписание, тарифы, скидки, таблица, статистика, прогноз, операции, заморозки, аудит и настройки.

Есть права менеджера на атлетов, продажи товаров, склад/товары, кассу и журнал согласно permission-контракту.

Админская заморозка поддерживает произвольную дату окончания, список активных заморозок, разморозку, автоматическое завершение и уведомления. Старый Telegram-сценарий не изменён.

### Client Web

- Кабинет, карточки атлетов, профиль, история, покупки, абонементы, заморозки.
- Создание атлета с CSRF/idempotency и привязкой к AuthContext.
- Расписание с дисциплиной, днём, временем, длительностью, тренером и группой.
- QR-пропуск с серверной подписью и почасовым сроком действия.
- Каталог товаров, тарифы, скидки, покупки и статус заказов.
- Оплата через YooKassa после серверной проверки заказа.
- Общий профиль Web ↔ Telegram.
- Staff-дашборд с активными сессиями и автообновлением.
- Глобальный поиск атлета по имени или телефону.
- Расширенная карточка атлета с раскрываемыми посещениями, оплатами и скидками.
- Сохранение фильтров списка атлетов и мобильная нижняя панель навигации.

### Telegram → Web привязка

В Telegram-профиле добавлена кнопка `🔐 Привязать Web-кабинет`.

Сценарий:

1. Telegram WebApp передаёт `initData` на `/auth/telegram/exchange`.
2. Сервер создаёт Web-сессию для подтверждённого Telegram-пользователя.
3. Клиент открывает `/auth/email-profile`.
4. Email подтверждается OTP и записывается в того же `User`.
5. Следующий вход по email открывает тот же клуб, тех же атлетов и покупки.

## Проверки

Последний локальный результат: `512 passed`.

Дополнительно проверено:

- native auth и email binding contracts;
- CSRF, Redis, idempotency, audit, rate limit и scope contracts;
- Python compileall;
- `node --check static/web/components.js`;
- `node --check static/web/client.js`;
- `git diff --check`.

Последний staging smoke:

- API `/health`: 200;
- API `/ready`: 200;
- DB: `ok`;
- Redis: `ok`;
- API, DB, Redis и nginx healthy;
- ошибок/traceback после запуска не обнаружено.

## Что осталось до production

Production gate: Web mutation flags disabled until acceptance.
No direct push to `master`.
Do not push to `master`.

## Текущая точка продолжения — 24.08.2026

Последний запрос пользователя: довести Web до визуального и функционального соответствия Telegram.

Сделано в последнем блоке:

- check-in получил поиск атлета, поиск по телефону и два действия: «Без турникета» и «С турникетом»;
- в списке склада отображаются фото, описание, статус товара и отдельные действия «Включить/Выключить» и «Архивировать»;
- в форме создания товара есть загрузка JPEG/PNG/WebP до 5 МБ с CSRF, permission, feature flag и проверкой сигнатуры файла;
- фото передаётся в клиентский магазин и отображается в клиентской карточке товара;
- API check-in теперь возвращает телефоны и ищет по имени, телефону атлета и телефону родителя;
- добавлена контрактная проверка медиакаталога и телефонного поиска.

Обнаружено пользователем и пока НЕ закрыто:

- Web «Таблица» визуально не соответствует Telegram; требуется перенести структуру Telegram `templates/admin.html` (быстрый поиск, активные сессии, история, заморозки/спящие клиенты и сворачиваемые блоки) в native Web;
- Web «Прогноз» у пользователя не открывается — проверить staging Network/API и traceback, затем устранить реальную причину, не скрывая ошибку в UI;
- на странице управления сотрудниками пропадает верхняя кнопка/меню «Разделы»; проверить общий монтаж навигации в `static/web/components.js`;
- не считать проект готовым к production до ручной проверки этих трёх страниц и полного staging smoke.

Последний локальный полный прогон после медиа-изменений был прерван пользователем; перед выкладкой обязательно выполнить:

```text
.\\venv\\Scripts\\python.exe -m pytest -q
node --check static/web/table.js
node --check static/web/forecast.js
node --check static/web/checkin.js
node --check static/web/catalog.js
node --check static/web/client.js
```

После зелёного локального прогона выкладывать только в `/root/speedycrm-staging`, пересобрать `speedycrm_staging_api`, проверить `/health`, `/ready` и логи без `error|traceback|critical|exception`. `master`, `/root/github.io-test` и ALTER не трогать.

- Ручной browser acceptance на desktop, iOS Safari и Android.
- Проверить реальный Telegram bridge на владельце/клиенте клуба 2.
- Проверить реальную оплату тестовым заказом YooKassa и webhook без боевых денег.
- Проверить повторный webhook, повторный submit, истечение сессии и cross-club denial.
- После acceptance отдельно принять решение о включении Web feature flags.
- Только после этого — review diff, backup/restore rehearsal, согласованный merge и отдельный production deploy.

Backup and rollback verification are mandatory before release. Separate explicit approval for production deployment is required.

Client rollout flags:

- `WEB_CLIENT_STUDENT_MUTATIONS_ENABLED=1` — enable only after acceptance;
- `WEB_CLIENT_BIND_PHONE_ENABLED=1` — enable only after acceptance;
- `WEB_SCHEDULE_MUTATIONS_ENABLED=1` — enable only after schedule acceptance.

Web mutation flags disabled by default in source and staging until the release gate is passed.

## Правило релиза

Не пушить Web-ветку в `master`, не обновлять `/root/github.io-test` и не менять ALTER. Любой production-релиз делать отдельным явно подтверждённым шагом после staging и ручного acceptance.

## Handoff для следующего чата

Начинать работу только в:

```text
C:\Users\79615\PycharmProjects\aaaa
```

Проверить ветку командой `git branch --show-current`. Ожидаемая ветка — `web-migration/phase-0-auth`.

Перед изменениями:

1. Прочитать этот файл целиком.
2. Проверить `git status --short` и не удалять чужие изменения.
3. Запустить targeted tests для затрагиваемого блока.
4. После каждого функционального шага добавить contract/regression test.
5. Перед staging запускать полный `venv\\Scripts\\python.exe -m pytest -q`.

Последняя подтверждённая точка:

- полный suite: `513 passed`;
- native Web bridge Telegram → email binding реализован;
- client renderer, staff UX, поиск, дашборд, карточка атлета и mobile navigation реализованы;
- staging healthy, DB/Redis `ok`;
- production и `master` не обновлялись.

Следующая безопасная точка старта — ручной browser acceptance на staging. После него проверять только найденные реальные дефекты, не переписывать работающие Telegram-сценарии.

## Идеи на будущее

Это идеи, а не незакрытые production-блокеры:

- единый центр уведомлений с приоритетами и отметкой «прочитано»;
- быстрый поиск с горячей клавишей `Ctrl+K` и поиском по имени, телефону, ID и email;
- закреплённые фильтры: «заканчиваются через 7 дней», «замороженные», «без посещений», «нулевой баланс»;
- календарь расписания в виде недели с фильтрами по секции и тренеру;
- drag-and-drop расписание после отдельного acceptance и проверки конфликтов;
- журнал изменений прямо в карточке атлета;
- экспорт текущего отфильтрованного списка в CSV/Excel;
- график посещаемости и выручки на главной с выбором периода;
- роль кассира в режиме «только продажи» с минимальным меню;
- offline-friendly экран последнего состояния для мобильного устройства без права изменять данные;
- PWA-установка Web-кабинета на телефон;
- health/status панель для владельца: Telegram, Redis, платежи, турникет и последние ошибки;
- интеграционные browser-тесты Playwright после стабилизации ручных сценариев.
