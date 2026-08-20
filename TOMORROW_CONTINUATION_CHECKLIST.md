# SpeedyCRM Web migration — continuation for tomorrow

## Start here

Работаем только в проекте:

```text
C:\Users\79615\PycharmProjects\aaaa
```

Рабочая ветка:

```text
web-migration/phase-0-auth
```

Последние коммиты:

- `b93715f` — левое выезжающее меню и общий monochrome visual system.
- `40fa2c8` — recovery-инструкции.
- `46b0dca` — зафиксированы пропущенные staff-разделы.
- `4002845` — зафиксировано, что Web Forecast/Table/Stats пока урезаны относительно Telegram.

Локальная проверка до визуального drawer-пакета: `491 passed`, `node --check` и `git diff --check` успешны.

## Что не трогать

Никогда не работать с:

- `master`;
- production/live;
- `/root/github.io-test`;
- `/root/alter`;
- `gym_db`;
- ALTER-контейнерами.

Другие проекты не пересобирать, не останавливать и не менять. Все изменения сначала делать локально в `aaaa`, затем при необходимости отправлять только в staging.

## Staging

Сервер:

```text
root@77.73.131.175
```

Каталог и compose:

```text
/root/speedycrm-staging
compose project: speedycrm-staging
```

Контейнеры:

```text
speedycrm_staging_api
speedycrm_staging_db
speedycrm_staging_redis
speedycrm_staging_nginx
```

URL:

```text
https://staging.speedycrm.ru:18443
```

Туннель в отдельном окне PowerShell:

```powershell
ssh -N -L 18000:127.0.0.1:18000 -i C:\Users\79615\.ssh\alter_agent root@77.73.131.175
```

Проверка на сервере:

```powershell
ssh root@77.73.131.175
cd /root/speedycrm-staging
docker compose -p speedycrm-staging -f docker-compose.staging.yml ps
curl -fsS http://127.0.0.1:18000/health
curl -fsS http://127.0.0.1:18000/ready
curl -kfsS https://staging.speedycrm.ru:18443/ready
```

Пересборка только staging:

```bash
cd /root/speedycrm-staging
docker compose -p speedycrm-staging -f docker-compose.staging.yml up -d --build
```

Не использовать `down --volumes`. Не восстанавливать live dump в `gym_db`. Не выводить в чат OTP, cookies, tokens, Telegram init_data, payment secrets, bot tokens или содержимое `.env`.

Последний staging deploy получил SSH timeout, поэтому перед новым smoke обязательно проверить `ps`, `health`, `ready` и логи только staging-контейнеров.

## Главный архитектурный принцип

Telegram WebApp и browser Web должны использовать один backend, одну БД, одни services, одни расчёты, одни права и одинаковые mutation contracts.

```text
Telegram WebApp ─┐
                 ├─ shared services / database / permissions / calculations
Browser Web ─────┘
```

Не копировать backend и не создавать вторую бизнес-логику. Telegram handlers нельзя механически подключать к браузеру, если они завязаны на `init_data`, callback-кнопки или Telegram WebApp API. Общую логику нужно вынести в сервисы, а Telegram/Web оставить адаптерами авторизации, транспорта и UI.

Целевая структура:

```text
services/students.py
services/subscriptions.py
services/sales.py
services/statistics.py
services/forecast.py
services/turnstile.py
services/notifications.py

auth/telegram_context.py
auth/web_context.py

Telegram adapters + Web API adapters
Telegram templates + Web templates
```

Пути Web можно оставить понятными (`/staff/forecast`, `/staff/students`, `/client/cabinet`). Старые Telegram/legacy пути (`/admin`, `/stats`, `/forecast`, `/admin/sales`) не ломать: сохранить их как aliases/legacy UI до завершения миграции.

## Что обнаружено

Backend routes Web для `/staff/forecast`, `/staff/students`, `/staff/overview`, `/staff/revenue`, `/staff/audit` существуют, но текущие страницы являются урезанными proof-of-concept.

Legacy Telegram WebApp намного информативнее:

- `/admin` / `templates/admin.html` — полноценная `📋 Таблица WebApp` с фильтрами, атлетами, балансами, абонементами, посещениями и действиями.
- `/stats` / `templates/stats.html` — полноценная статистика: периоды, дисциплины, активность, churn, top athletes, выручка, расходы, margin и Excel export.
- `/forecast` / `templates/forecast.html` — фильтры, таблица прогнозируемых атлетов, продления, тарифы, дисциплины, revenue/visit charts.
- `/admin/sales` — журнал последних операций с фильтрами, итогами, клиентом/атлетом, способом оплаты, деталями и действиями.

Текущий Web пока показывает только KPI/list shells. `Audit` — технический журнал и не заменяет журнал финансовых операций.

## План на завтра

1. Подтвердить ветку, чистый статус и staging health.
2. Составить таблицу соответствия: Telegram page → существующий handler/service → Web route/API → missing UI/actions.
3. Найти общую бизнес-логику и не дублировать её в Web.
4. Перенести полноценную `Таблица WebApp`.
5. Перенести полноценную `Статистика`.
6. Перенести полноценный `Прогноз` с таблицами, фильтрами и графиками.
7. Перенести `Последние операции` отдельно от Audit.
8. Затем пройти все страницы: admin/owner cabinet, client cabinet, athletes, subscriptions, freezes, sales, cash, schedule, settings, schedulers, disciplines, notifications, broadcast, turnstile, audit and passkeys.
9. Сделать русскую локализацию всех заголовков, labels, buttons, errors, empty/loading/success states и accessibility labels.
10. Сохранить общий стиль: белый фон, чёрный текст/controls, серый secondary text, единые cards/inputs/buttons, левый off-canvas drawer, без horizontal overflow.

## Проверки после каждого пакета

```powershell
git status --short --branch
node --check static/web/components.js
git diff --check
.\venv\Scripts\python.exe -m pytest -q
```

После каждого пакета обновить:

- `MIGRATION_PROGRESS.md`;
- `WEB_MIGRATION_CONTINUATION_CHECKLIST.md`;
- `WEB_ACCEPTANCE_REPORT.md`;
- этот файл, если изменились команды, ограничения или следующий шаг.

После обновления документов сделать отдельный commit. Не merge/push в `master` без явного разрешения.

## Acceptance для каждой страницы

- Та же выборка данных и те же расчёты, что в Telegram.
- Та же клубная изоляция и permission boundary.
- GET/data загружается и отображает реальные записи.
- Каждая кнопка и форма проверены: success, error, loading, empty, duplicate/retry.
- Mutation имеет CSRF, server-side scope, idempotency и audit.
- Детальные страницы и back-links существуют.
- Desktop/mobile layout не ломается.
- Русская локализация не содержит склеенных слов, mojibake или английских placeholder-ов без причины.
- Результат записан в acceptance report со статусом и причиной любого pending.

## Безопасность и секреты

Не хранить и не отправлять в чат приватные ключи, `.env`, OTP, cookies, access tokens, Telegram `init_data`, bot/payment secrets. Staging bot token не брать из live; broadcast smoke требует отдельного одобренного staging token.

