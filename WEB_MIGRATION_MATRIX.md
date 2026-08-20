# Web migration matrix

Branch: `web-migration/phase-0-auth`

## Read-only coverage

| Area | Web API | Web page | Permission/scope | State |
|---|---|---|---|---|
| Auth | `/auth/me`, exchange, logout | — | server session | done |
| Forecast | `/api/v1/staff/forecast/data` | `/staff/forecast` | `forecast_view`, club | done |
| Revenue | `/api/v1/staff/revenue/data` | `/staff/revenue` | `analytics_view`, club | done |
| Overview | `/api/v1/staff/overview/data` | `/staff/overview` | `analytics_view`, club | done |
| Students | `/api/v1/staff/students/data` | `/staff/students` | `analytics_view`, club | done |
| Cash | `/api/v1/staff/cash/data` | `/staff/cash` | `cash_view`, club | done, read-only |
| Sales | `/api/v1/staff/sales/data` | `/staff/sales` | `analytics_view`, club | done, read-only |
| Audit | `/api/v1/staff/audit/data` | `/staff/audit` | `analytics_view`, club | done, read-only |
| Schedule | `/api/v1/staff/schedule/data` | `/staff/schedule` | `schedule_view`, club | done, read-only |
| Products | `/api/v1/staff/catalog/products` | `/staff/products` | `products_view`, club | done, read-only |
| Discounts | `/api/v1/staff/catalog/discounts` | `/staff/discounts` | `analytics_view`, club | done, read-only |
| Tariffs | `/api/v1/staff/catalog/tariffs` | `/staff/tariffs` | `tariffs_manage`, club | done, read-only |
| Client cabinet | `/api/v1/client/cabinet/data` | `/client/cabinet` | user + club | done, read-only |
| Client history | `/api/v1/client/history/data` | `/client/history` | user + club | done, read-only |
| Client freeze status | `/api/v1/client/freeze/data` | `/client/freeze` | user + club | done, read-only |
| Client subscriptions | `/api/v1/client/subscriptions/data` | `/client/subscriptions` | user + club | done, read-only |

## Remaining areas

- Client mutations: buy subscription, buy freeze, bind phone, create student.
- Staff mutations: cash entry, sales, products, tariffs, discounts, schedule, freeze, audit deletion.
- Staff pages not yet represented by a dedicated Web contract: live camera/check-in, product sale flows, admin settings/legal.
- Browser UI still uses a functional shared shell; final visual design is intentionally deferred.

## Mutation gate

Before enabling any mutation in Web:

1. Require web `AuthContext` and server-side club lookup.
2. Require permission at the backend, not only in UI.
3. Require CSRF validation for POST/PATCH/DELETE.
4. Recalculate prices, discounts, ownership, and club scope server-side.
5. Add idempotency and concurrency tests where money or inventory changes.
6. Keep the legacy Telegram route unchanged and add a rollback/feature flag.

The reusable `require_csrf(redis, request)` guard is now available in `auth/web_session.py`. Logout already uses the same validation path.

First data mutations: `POST /api/v1/client/bind-phone` and `POST /api/v1/client/students`. Both use session identity/club, CSRF, idempotency, audit telemetry, and never accept `user_id` or `club_id` from payload.

Next mutation decision is intentionally pending. Money, inventory, subscription, freeze, schedule, and audit-delete mutations remain disabled in Web until their individual contracts are reviewed and tested.

## Verification

- Current full local suite: `318 passed`.
- Production was not contacted or deployed.
