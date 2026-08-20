# Web acceptance report

Дата контрольной точки: 2026-08-20  
Ветка: `web-migration/phase-0-auth`  
Production/master: не изменялись.

## Статус

Web-контур проверен локальными contract/unit/regression тестами и staging unauthenticated smoke. Полный локальный suite на предыдущем пакете: `475 passed`; после добавления QR pass требуется финальный повтор suite перед закрытием этой точки.

Формулировка «всё работает идеально» пока не используется: authenticated browser smoke вручную требует отдельного staging test account/Telegram bot или approved Email OTP mailbox. Без этого нельзя достоверно подтвердить фактическое отображение данных конкретного клиента в браузере.

Последний полный suite после QR pass: `476 passed`, `git diff --check` чист.

## A–Z функциональная матрица

| Контур | Web status | Проверка |
|---|---:|---|
| Web session login/logout/revocation | готов | auth/session/security tests + staging auth-gating |
| Client cabinet, subscriptions, history, summaries | готов по контрактам | club/user scoped tests |
| Client profile edit, email binding, phone binding | готов по контрактам | CSRF, rate-limit, idempotency tests |
| Client QR pass / Telegram-compatible QR payload | добавлен | HMAC, hourly salt, parent/club scope contract |
| Client freeze and online payment intent | готов по контрактам | ownership/status/amount/provider guards |
| Client product/catalog/discount/tariff views | готов по контрактам | scoped read-only tests |
| Staff overview/forecast/revenue/students | готов по контрактам | permission/club scope tests |
| Cash income/expense/reversal | готов | CSRF, permission, lock, idempotency, audit tests |
| Product create/edit/archive/stock/sale | готов | stock/discount/money safety tests + UI selectors |
| Subscription/freeze sale | готов | tariff/student selectors + backend safety tests |
| Schedule editing | готов | allowlist, schedule_edit, CSRF/idempotency tests |
| Staff/client settings | готов | settings permissions, safe fields, no secrets in UI |
| Check-in/manual turnstile/cancel | готов по backend/UI contracts | common gate service, permission, club scope tests |
| QR scanner/FaceID staff pass | legacy path preserved; native Web parity needs authenticated smoke | Telegram WebApp tests, no native Web claim yet |
| Payment webhook | hardened | success, wrong amount/metadata, duplicate, retry, currency matrix |
| Audit | read/search/detail ready; deletion intentionally absent in Web | policy contract test |

## Проверки окружений

- Local: full pytest suite and `git diff --check` are mandatory before closing the next checkpoint.
- Staging unauthenticated smoke: `/health=200`, `/ready=200`, `/auth/login=200`, `/auth/me=401`; all protected route checks returned `401` without a session.
- Staging flags/data: no production flags enabled and no test data created by the smoke.
- Real staging payment: not executed; requires approved provider test account/credentials.
- Manual authenticated browser/Email OTP smoke: pending separate test account/mailbox; it must verify client data, refresh, logout, QR pass, freeze, purchases and cross-club denial.

## Acceptance scenarios still required before any merge decision

1. Authenticate a synthetic staging client through approved Web login.
2. Verify cabinet students, subscriptions, visits, payments, discounts, QR pass and client profile all belong to the authenticated club/user.
3. Refresh browser, reopen every client menu item, submit one safe mutation in staging, and verify success/error/loading states.
4. Authenticate owner/staff roles and check settings, schedule, cash, catalog, sales, check-in and turnstile permission boundaries.
5. Attempt cross-club IDs and expired/invalid sessions; confirm denial without data leakage.
6. Run mocked provider payment success/failure matrix, then separately approved real staging payment and webhook retry.
7. Record screenshots/HTTP status/results here, then rerun full suite and perform human diff review.

No merge to `master` or production rollout is authorized by this report.
