# Web acceptance report

Дата контрольной точки: 2026-08-20  
Ветка: `web-migration/phase-0-auth`  
Production/master: не изменялись.

## Статус

Web-контур проверен локальными contract/unit/regression тестами, staging unauthenticated smoke и ранее выполненным authenticated Email OTP/owner smoke. После добавления QR pass полный локальный suite: `476 passed`.

Authenticated owner login, client/owner API requests, session, logout/re-auth и club/user scope ранее проверялись вручную на staging. Поэтому auth smoke не является pending. Реальный оставшийся риск — UI rendering: часть страниц в том smoke была пустой после получения данных; нужен повторный browser pass именно на отображение каждой страницы и кнопок после текущих исправлений.

Последний полный suite после scheduler/turnstile controls: `483 passed`, `git diff --check` чист.

Staff Products/Discounts/Tariffs теперь имеют mutation forms; их visual browser pass входит в следующий authenticated acceptance cycle.
Cash reversal selector, audit detail links и staff student hub links добавлены и покрыты contracts.
Settings Camera mutation и Menu flags теперь имеют Web controls/API.
Staff management role/active/permission editing теперь использует club-scoped staff selector.
Scheduler flags and Turnstile configuration now have Web controls; real relay pulse still requires isolated hardware staging smoke.

Последний UI gap был не в API: client pages показывали данные недостаточно функционально — общий renderer выводил только счётчик. Добавлен общий data-table renderer; после него нужен повторный browser pass кнопок и mutation flows.

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
| Native browser Face ID/passkey | passed on staging | Authenticated owner on iOS Safari 17.6.1 registered a passkey successfully and confirmed revoke; only credential ID/public key/counter metadata is stored |
| Payment webhook | hardened | success, wrong amount/metadata, duplicate, retry, currency matrix |
| Audit | read/search/detail ready; deletion intentionally absent in Web | policy contract test |

## Проверки окружений

- Local: full pytest suite and `git diff --check` are mandatory before closing the next checkpoint.
- Staging unauthenticated smoke: `/health=200`, `/ready=200`, `/auth/login=200`, `/auth/me=401`; all protected route checks returned `401` without a session.
- Latest staging hardware attempt: blocked before request because `staging.speedycrm.ru:18443` was unavailable (`/health` HTTP 0 / connection failure); no relay command or staging mutation was sent.
- После восстановления staging текущий commit был пересобран только в `/root/speedycrm-staging`; health/ready smoke снова зелёный, новые scheduler/turnstile/menu/client-pass routes корректно gated `401` без сессии.
- Staging flags/data: no production flags enabled and no test data created by the smoke.
- Real staging payment: not executed; requires approved provider test account/credentials.
- Real staging turnstile pulse: passed after staging restore. One direct pulse was sent through `speedycrm_staging_api` using club 2 configuration; no student was selected and no visit/CRM record was created.
- Latest staging rebuild: passed; isolated staging API/DB/Redis/nginx are running, `/health=200`, `/ready=200`, and Alembic head is `a1b2c3d4e5f6`.
- Owner Email OTP fixture restored in isolated staging only: owner is in club 2 and the approved test email is bound; authenticated browser/Face ID smoke remains pending.
- Owner smoke found and fixed missing `/staff/profile` page; route is now deployed and unauthenticated access correctly returns `401`.
- Owner smoke found and fixed WebAuthn double-prefix 404; authenticated endpoint path is now deployed and correctly auth-gated (`401` without session).
- Latest WebAuthn log issue fixed: binary challenge options are now serialized as base64url instead of UTF-8; staging rebuilt, browser retry required.
- Safari compatibility adjustment deployed: registration advertises ES256/RS256 and preferred user verification; iOS 17.6.1 retry pending.
- Added non-persisting authenticated diagnostic page `/staff/passkey-debug` to capture browser error before registration complete.
- Diagnostic result `InvalidCharacterError` fixed: base64url padding is now calculated correctly before passing challenge/user ID to Safari.
- Text broadcast package is deployed to isolated staging; unauthenticated page gate returns `401`. No real broadcast was sent.
- Saved payment method package is deployed to isolated staging; no real card token mutation was executed during smoke.
- Hardware result is limited to the explicitly approved staging/work-area relay test; production/master were not involved.
- Manual authenticated owner/client Email OTP/API smoke: previously passed and recorded in `MIGRATION_PROGRESS.md` (including owner session, `auth_source=email`, club scope and logout).
- Manual visual browser re-check: owner passkey/profile flow passed; remaining page-by-page client/staff mutation visual sweep is still pending.

## Acceptance scenarios still required before any merge decision

1. Reopen the already-tested authenticated client/owner sessions (or repeat Email OTP if the fixture was cleaned).
2. Verify cabinet students, subscriptions, visits, payments, discounts, QR pass and client profile visibly render and belong to the authenticated club/user.
3. Refresh browser, reopen every client menu item, submit one safe mutation in staging, and verify success/error/loading states rather than only HTTP responses.
4. Authenticate owner/staff roles and check settings, schedule, cash, catalog, sales, check-in and turnstile permission boundaries.
5. Attempt cross-club IDs and expired/invalid sessions; confirm denial without data leakage.
6. Run mocked provider payment success/failure matrix, then separately approved real staging payment and webhook retry.
7. Record screenshots/HTTP status/results here, then rerun full suite and perform human diff review.

No merge to `master` or production rollout is authorized by this report.

## Continuation checkpoint

The full recovery context is maintained in `WEB_MIGRATION_HANDOFF.md`. Subscriptions and freezes are backed by existing database/backend services and are wired into Web reads/mutations; scheduler notifications remain backend/Telegram delivery, while Web exposes settings/status. Web now has a text-only owner/staff broadcast composer, client saved-payment-method revoke, receipt delivery and owner/staff invitations. Media-copy remains pending; refunds require a provider-backed YooKassa integration and explicit policy, not a status-only mutation. Staging and forbidden-target boundaries are documented and must be preserved.

## Remaining after CRM functional migration

1. Deploy migration and complete authenticated browser WebAuthn registration/assertion/revoke smoke for client/staff.
2. Remaining legacy UI policies: refunds, payment-method changes, receipt delivery, invitations and any explicitly approved audit retention action.
3. Full EN/RU translations and final visual/mobile accessibility polish.
4. Authenticated page-by-page browser re-check after the latest UI package; real staging payment provider test.
5. Final Telegram regression, human diff review, backup/rollback review and explicit merge approval.
