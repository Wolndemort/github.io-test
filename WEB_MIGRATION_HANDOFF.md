# SpeedyCRM Web Migration Handoff

Приватный handoff для нового AI-чата. Файл добавлен в .gitignore и не должен попадать в Git. Секреты не хранить.

## Цель

Постепенно вынести все staff/client страницы из Telegram WebApp в обычный Web, сохранив один backend, одну БД, платежи, роли, скидки, СКУД и Telegram-уведомления. Telegram не удаляем: Telegram, browser и mobile используют одни API и services.

## Проект

Локально: C:\Users\79615\PycharmProjects\aaaa

Git: https://github.com/Wolndemort/github.io-test, branch master

Production:

- SSH key: C:\Users\79615\.ssh\alter_agent
- command: ssh -i C:\Users\79615\.ssh\alter_agent root@77.73.131.175
- project: /root/github.io-test
- API container: gym_crm_api
- database: gym_db
- redis: githubio-test-redis-1

Другой проект ALTER: C:\Users\79615\PycharmProjects\Alter и /root/alter. Не смешивать.

Health:

    curl -k https://speedycrm.ru/health
    curl -k https://speedycrm.ru/ready
    cd /root/github.io-test
    docker compose ps
    docker logs --tail=200 gym_crm_api

Перед прямым deploy:

    mkdir -p backups
    docker exec gym_db pg_dump -U postgres -d crm_db > backups/pre_web_migration_$(date +%F_%H-%M-%S).sql

Обычный deploy запускается GitHub Actions после push master. Проверять Actions и production commit отдельно.

## Ключевые файлы

- main.py — FastAPI, routers, scheduler, Sentry.
- admin_module/api.py — основные staff pages, cash, sales, revenue.
- admin_module/admin_pages.py — forecast и analytics pages.
- admin_module/webapp_client_cabinet.py — client/staff cabinet.
- admin_module/webapp_views.py — WebApp views.
- admin_module/utils.py — Telegram gates, club lookup, staff auth.
- admin_module/webapp_verify.py — Telegram init data verification.
- services/staff_permissions.py — roles/permissions.
- services/discounts.py — assigned discounts and scopes.
- services/analytics.py — calculations and forecast.
- database/db.py — models/session.
- templates — current WebApp HTML.

Existing pages to move:

    /webapp/client-cabinet
    /admin
    /revenue
    /forecast
    /admin/cash
    /admin/sales
    /admin/students
    /webapp/admin-cash-subscription
    /webapp/admin-product-sale
    /webapp/admin-products
    /webapp/admin-schedule
    /webapp/admin-tariffs
    /webapp/admin-discounts
    /webapp/admin-freeze
    /webapp/admin-audit
    /webapp/staff-checkin

## Target architecture

## UI visual reference

Для нового Web-интерфейса использовать визуальный паттерн страницы Forecast:

- дорогая monochrome-палитра: белый, чёрный, серые оттенки;
- крупная типографика и спокойная иерархия блоков;
- аккуратные rounded cards без визуального шума;
- чёрный hero-блок с контрастным заголовком;
- компактные KPI-карточки;
- line charts с понятными tooltip;
- responsive mobile-first layout;
- одинаковые состояния кнопок, таблиц, фильтров и пустых списков;
- минималистичная премиальная подача в стиле текущего Forecast.

Forecast — основной visual reference для выноса страниц в Web.

Один backend:

    Telegram WebApp -> same API/services
    Browser Web    -> same API/services
    Mobile         -> same API/services

Нужен общий AuthContext:

    user_id
    club_id
    actor_type
    role
    permissions
    auth_source

Ввести providers:

- TelegramProvider: текущий init_data.
- WebSessionProvider: server-side session + HttpOnly cookie.
- MobileBearerProvider: Authorization Bearer + refresh token.

Общий вход:

    current_actor = await authenticate_request(request)

Порядок: web session, bearer token, Telegram init_data, иначе 401.

Нельзя доверять club_id из query string. Основной club context берётся из session/host, а каждый запрос проверяет actor access to club.

## Web auth

Добавить:

- /auth/login
- /auth/logout
- /auth/me
- /auth/telegram/exchange

После входа через Telegram делать one-time exchange в web session и redirect на короткий URL. Не хранить init_data в постоянной ссылке.

Cookie: HttpOnly, Secure, SameSite=Lax, expiration, idle timeout, rotation after login, revoke on logout. Для POST/PATCH/DELETE — CSRF. Session хранить в Redis или БД, не в неподписанном cookie.

## Domains

Переходный вариант:

    speedycrm.ru
    club-2.speedycrm.ru/staff
    club-2.speedycrm.ru/staff/forecast
    club-2.speedycrm.ru/staff/cash

Telegram page should use Telegram.WebApp.openLink('/staff/forecast') to open external browser. Web and Telegram keep the same backend route/service contracts.

## Roles

Important permissions:

    cash_sale, cash_view, products_view, products_manage
    forecast_view, analytics_view
    schedule_view, schedule_edit, tariffs_manage
    qr_checkin, manual_checkin

Owner and super_admin: full access.
Manager: cash, analytics, table, revenue, forecast, sales journal.
Cashier: permitted sales/cash/forecast only.
Coach: operational check-in/schedule, no finance unless explicitly granted.

Backend must check permission; hiding a button is not authorization.

## Discounts and totals

Scopes are isolated:

    subscriptions — subscriptions only
    products       — products only
    freeze         — freezes only
    all            — explicit universal discount

Assigned discounts apply automatically. Manual discounts may be added only after backend scope/club/date validation. UI preview is convenience; backend recalculates final amount.

Family rule currently: discount is applied per subscription line/student, not once to whole cart.

## Forecast

Services:

    calculate_projected_renewal_revenue
    build_expiry_series
    build_visit_series
    build_revenue_series

Forecast uses recent visits and expiry window. Tariff is selected separately per discipline from confirmed sales. Revenue chart: historical actual money plus future weekday-based estimate. Visits chart: actual visits by day. Each chart has its own independent date range and update action; the initial ranges may be equal for convenience, but one chart must never change the other. Forecast is probability, not guarantee.

## Long-term independence rules

The product is API-first. Web, mobile and Telegram are clients of the same domain services; none of them owns business rules or data. A UI may be replaced without changing payments, subscriptions, visits, discounts, permissions or audit semantics.

### Stable contracts

- Keep domain services independent from FastAPI, Telegram SDK, templates and mobile UI.
- Expose resource-oriented JSON endpoints for new Web/Mobile work; keep existing HTML/WebApp routes as compatibility adapters until migration is complete.
- Version breaking API changes (`/api/v1/...`); additive response fields are preferred over changing existing meanings.
- Use stable identifiers (`club_id`, `student_id`, order IDs) and explicit enums for status/source/method. Never use display text as a contract.
- Return one error shape with a machine-readable `code`, safe user-facing `message`, and optional field errors. Do not expose tracebacks or secrets.

### Identity and tenant isolation

- Authentication answers who the actor is; authorization answers what the actor may do; club context is checked separately on every request.
- Store a canonical internal user/actor identity. Telegram ID, email, phone and mobile installation IDs are external identities linked to it, not primary authorization rules.
- Never trust `club_id`, `student_id`, owner IDs or prices supplied by a client. Load them from the authenticated actor's allowed scope and recalculate all money server-side.
- Web sessions and mobile refresh tokens must be revocable, rotated, expiry-bound and audited. Logout must invalidate the server-side credential.
- Telegram remains a provider/fallback: removing Telegram must not remove a user, club, payment, visit or permission.

### Data and integration boundaries

- PostgreSQL is the source of truth. Redis is for cache, locks, rate limits and short-lived state only; every cache needs a safe miss path.
- All payment/webhook/check-in mutations must be idempotent and safe under retries and concurrent requests. Use database uniqueness/locks where correctness depends on them.
- Background jobs must be repeatable, timezone-explicit (`Europe/Moscow` for business dates), observable and protected from duplicate execution.
- Store uploaded files outside the application container with an abstraction for local/S3-compatible storage; persist metadata and access checks, not permanent filesystem paths.
- External providers (YooKassa, turnstile, Telegram, push, email) require adapters, timeouts, retries with bounds, and a failure state that does not corrupt business data.

### Delivery and migration safety

- Every schema change uses a new Alembic migration and is backward-compatible with the previous application during rolling deploys.
- New endpoints require contract tests, cross-club tests, auth-provider tests, idempotency tests and a smoke-check before production.
- Keep compatibility telemetry during migration: route, client type, auth source, latency, status and error code; never log tokens, init data, payment secrets or personal data unnecessarily.
- Roll out Web and Mobile behind explicit feature flags, with a documented rollback path that leaves the Telegram client working.

## ALTER reference

## Latest recovery note — 2026-08-20

Continue only in `C:\Users\79615\PycharmProjects\aaaa` on branch `web-migration/phase-0-auth`. Latest local visual commit is `b93715f`; local validation is `491 passed`. The current work package is the shared monochrome style plus the left off-canvas navigation drawer. Before continuing, verify staging connectivity and containers; the last SSH deployment command timed out and must not be assumed healthy. Staging alone is `/root/speedycrm-staging` with compose project `speedycrm-staging`. Do not touch `master`, live, `/root/github.io-test`, `/root/alter`, `gym_db` or ALTER containers.

Next objective is a complete Telegram CRM → Web inventory, Russian localization and page-by-page functional audit. Telegram currently contains materially richer/informative pages, and some Web pages/operations are still missing; identify them from the actual menu and backend routes before more visual polishing. Every package must update the progress, continuation checklist and acceptance report.

## Continuation checkpoint — 2026-08-20

Current local project: `C:\Users\79615\PycharmProjects\aaaa`, branch `web-migration/phase-0-auth`.
Latest verified commit: `798a054` (successful Safari passkey smoke); later diagnostic/base64 fixes are in the local history through `0f6b649` and `603d4ce`.

Staging only:

- SSH host: `root@77.73.131.175`, directory `/root/speedycrm-staging`, compose project `speedycrm-staging`.
- Containers: `speedycrm_staging_api`, `speedycrm_staging_db`, `speedycrm_staging_redis`, `speedycrm_staging_nginx`.
- HTTPS: `https://staging.speedycrm.ru:18443`; API health/ready are checked through the isolated staging bind.
- DB head: `a1b2c3d4e5f6`; WebAuthn flag and staging mail flags are enabled only in `/root/speedycrm-staging/.staging-mail.env`.
- Owner smoke fixture: owner is in club 2; the approved staging email is bound; do not print OTP or mail secrets.
- Verified: owner Email OTP login, staff profile navigation, iOS Safari 17.6.1 passkey registration and revoke, turnstile pulse, and full local suite `483 passed`.

Never touch: local `master`, production/live, `/root/github.io-test`, `/root/alter`, `gym_db`, or any ALTER containers. Do not push/merge to `master` without explicit approval. After every package update `MIGRATION_PROGRESS.md`, `WEB_MIGRATION_CONTINUATION_CHECKLIST.md`, and `WEB_ACCEPTANCE_REPORT.md`.

Database/backend status: subscriptions use the existing `subscriptions` table plus student expiry/balance fields and are read by client Web endpoints and activated by staff cash-sale backend. Freezes use existing student freeze fields and `process_student_freeze`/`purchase_student_freeze`; client purchase and staff read-only freeze Web paths are wired and club/feature/idempotency/audit guarded. Notifications and scheduler reminders remain backend-driven through `services/scheduler_jobs.py`; Web exposes notification status/data and scheduler settings, while Telegram remains the delivery channel. Broadcast sending itself is still a legacy Telegram operation and has no equivalent Web composer/send flow yet. Remaining explicit legacy gaps include refunds, payment-method changes, receipt delivery, invitations, and full visual page-by-page sweep.

Reference project: C:\Users\79615\PycharmProjects\Alter

Useful paths:

- Alter/api/auth_routes.py — bearer helper pattern.
- Alter/middleware/guard_middleware.py — auth guard.
- Alter/middleware/db_middleware.py — DB session middleware.
- Alter/api — API route organization.
- Alter/web — web UI.
- Alter/mobile — mobile client.
- Alter/nginx.alter.conf — nginx/domain reference.
- Alter/PRICING.md — positioning.
- Alter/PRODUCTION_READY.md — production checklist.
- Alter/.env.example and .env.production.example — variable naming.

Do not copy ALTER auth blindly. Preserve Telegram provider and add web session beside it.

## Migration phases

Phase 0: AuthContext, web session, auth/me/logout, cross-club tests. Do not break Telegram.

Phase 1: browser forecast proof of concept, session auth, openLink, desktop smoke test.

Phase 2: staff cabinet, table, revenue, forecast, cash, sales journal.

Phase 3: cash subscription, products, tariffs, discounts, check-in, freeze, audit.

Phase 4: mobile bearer/refresh/revoke and push provider independent from Telegram.

## Acceptance criteria

- Telegram, browser and mobile see the same data.
- One permission system.
- Cross-club access tests pass.
- Same totals and discount scopes in every interface.
- No permanent init_data in web URLs.
- Logout revokes access.
- All mutations protected server-side.
- Full tests, smoke-check, health, ready and production logs are clean.

## New chat prompt

Работаем в C:\Users\79615\PycharmProjects\aaaa. Используй WEB_MIGRATION_HANDOFF.md. Нужно начать Phase 0: общий AuthContext и web session для существующего FastAPI backend. Telegram не удалять. Сначала провести аудит auth, permissions и cross-club isolation; не деплоить production до локальных тестов.

Never expose private keys or .env contents in chat or Git.

## Visual direction for the Web migration

Use the Forecast page as the visual reference for every new Web page. The target is a premium monochrome interface: white, black and restrained gray tones; large confident typography; generous spacing; rounded cards; a high-contrast hero block; compact KPI cards; clean tables; and interactive charts with clear tooltips. Keep layouts responsive and polished on desktop and mobile. Reuse the same visual language for navigation, buttons, filters, loading states and empty states so the Web product feels like one coherent system.

The Forecast page is the source of truth for the visual patterns. New pages should extend its components and tokens instead of introducing unrelated colors, browser-default controls or one-off layouts. The product name is ALTER; Forecast is the premium analytics reference experience.
