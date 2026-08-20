# SpeedyCRM Web migration — continuation checklist

Этот файл нужен для продолжения работы после лагов/перезапуска чата.

## Контекст

- Проект: SpeedyCRM, репозиторий `C:\Users\79615\PycharmProjects\aaaa`.
- Base branch: `master`.
- Working branch: `web-migration/phase-0-auth`.
- Production project: `/root/github.io-test` — не изменять.
- ALTER project: `/root/alter` — не изменять.
- Staging project: `/root/speedycrm-staging` — единственное серверное окружение для smoke.
- Staging API: SSH tunnel `127.0.0.1:18000`.
- Tunnel: `ssh -N -L 18000:127.0.0.1:18000 -i C:\Users\79615\.ssh\alter_agent root@77.73.131.175`.
- Never merge/push to `master` without explicit user approval; push to `master` triggers production workflow.

## Current checkpoint

- Current branch HEAD after this checklist commit: see `git rev-parse HEAD`.
- Latest verified full suite before this checklist: `460 passed`.
- `git diff --check` must remain clean.
- All work is local branch only; live/ALTER/master untouched.

## Completed functional Web blocks

- Web AuthContext, Telegram fallback, Email OTP, Redis sessions, CSRF, logout/revocation, club isolation.
- Read-only staff/client pages and shared navigation/EN-RU selector.
- Student create/update, manual check-in, cancel visit, turnstile option.
- Cash income/expense and reversal.
- Product create/update/archive, stock adjustment, cash product sale.
- Cash subscription sale and paid freeze.
- Club settings mutation: branding, limits, features, menu flags, integrations booleans.
- Discount create/update/assignment and tariff mutation.
- Staff create/update.
- Online payment intent, redirect UI and webhook safety contracts.
- Staging isolation, backup/restore test DB, migration downgrade/upgrade, rollback runbook.

## Remaining A-to-Z work

### Functional backend

- [ ] Verify and complete all legacy operations against Web AuthContext, not Telegram `init_data`.
- [x] Authenticated owner/client Email OTP/API smoke previously completed and recorded; visual page-by-page browser re-check remains.
- [x] Client collection pages render functional returned records/tables with empty/error states.
- [x] Staff Products/Discounts/Tariffs pages expose functional mutation forms wired to scoped APIs.
- [x] Staff Cash reversal, Audit detail links and Student hub navigation are functional.
- [x] Camera mutation and Menu flags settings pages are wired to scoped APIs.
- [x] Staff management editor uses authenticated staff selector for role, active state and permissions.
- [x] Scheduler flags and safe Turnstile configuration controls are wired to Web APIs; real relay hardware smoke remains staging-only.
- [x] Real staging turnstile pulse smoke passed after isolated staging restore; no student/visit record was created.
- [x] Native browser Face ID via WebAuthn/passkeys: store credential ID/public key only; keep Telegram BiometricManager flow unchanged.
- [x] Schedule editing UI and backend contract smoke.
- [x] Discipline configuration mutation with safe schedule block allowlist.
- [x] Camera/turnstile configuration controls without exposing device secrets.
- [x] Notification preference mutation for safe boolean flags.
- [x] Staff list UI, create/update backend, permissions editor and edit form.
- [x] Client profile name editing; staff profile/user admin extensions remain.
- [x] Discount assignment list/removal backend; UI selector polish remains.
- [x] Product sale UI with scoped product, buyer and discount pickers.
- [x] Subscription/freeze UI with scoped tariff/student selectors.
- [x] Online payment provider integration smoke with mocked provider; approved real staging payment test remains pending.
- [x] Webhook integration matrix contracts for success, wrong amount, wrong metadata, duplicate and provider retry.
- [x] Add text-only Web owner/staff broadcast composer with club scope, permission, CSRF, idempotency and audit.
- [ ] Add media-copy mode and verify text broadcast on isolated staging.
- [x] Deploy text broadcast package to isolated staging and verify auth gate without sending a real message.
- [x] Deploy saved payment method package to isolated staging; authenticated mutation smoke remains pending.
- [x] Deploy receipts/invitations package to isolated staging without sending external messages.
- [x] Audit static client/staff/settings navigation links against registered FastAPI routes; dynamic detail links verified as parameterized routes.
- [x] Audit admin mutation calls against registered backend routes and confirm CSRF/idempotency/scope contract coverage.
- [x] Audit settings form state; populate current GET values before any mutation to prevent accidental resets.
- [x] Audit client ownership for student update/freeze/payment intent; linked-parent and order/user/club scopes are covered by tests.
- [x] Deploy client ownership fix to isolated staging.
- [x] Unify client read scope across cabinet/history/freeze/subscriptions/summaries for primary and linked parents.
- [x] Deploy linked-parent scope fix to isolated staging.
- [x] Audit client purchases and include confirmed CartOrder + PaymentOrder sources under user/club scope.
- [x] Audit client profile data; return scoped full_name/email and populate edit form without changing minimal auth contract.
- [x] Deploy client profile package to isolated staging.
- [x] Audit client freeze display and derive frozen_until from stored frozen_at/frozen_days.
- [x] Audit multi-form admin panels; secondary Products/Discounts buttons now submit through their backend contracts.
- [x] Verify cash and check-in secondary forms have explicit handlers (reversal/cancel).
- [x] Audit owner/staff/client role and feature-gate contracts for legacy Web operations; regression assertions added.
- [x] Add GET data endpoints for notifications and disciplines after staging `405` log review.
- [x] Start shared visual pass: white background, black contrast, unified controls and compact dropdown navigation.
- [ ] Complete authenticated visual review of every page after CSS deployment.
- [ ] Configure a separate approved staging bot token before real broadcast smoke; never reuse live token.
- [x] Deploy settings state fix to isolated staging.
- [ ] Any remaining legacy operations: refunds, payment method changes, receipt delivery, audit search/delete policy, invitations.
- [x] Add client Web saved payment method list and safe revoke using subscription row lock/club scope/audit.
- [ ] Add refunds, receipt delivery and Web invitations.
- [x] Add receipt delivery endpoint for confirmed current-user orders.
- [x] Add owner/staff invitation endpoint with phone/slot validation and bot link generation.
- [ ] Add provider-backed refunds only after refund policy and YooKassa API integration are approved.
- [x] Client phone binding UI wired to the existing rate-limited Web endpoint; email binding UI already present.
- [x] Client QR pass UI/API with authenticated parent/club scope and Telegram-compatible hourly HMAC payload.

### Frontend

- [x] Replace remaining temporary payment order ID input with authenticated pending-order selector.
- [x] Add shared loading/aria-busy state; mutation forms retain explicit error/success messages.
- [x] Keep async staff summaries separate from mutation mount points so operation buttons survive loading.
- [x] Add confirmation guard for money, archive, reversal, cancellation and freeze/sale mutations.
- [ ] Full EN/RU translations; current selector changes language state but is not a complete translation system.
- [ ] Final shared design polish and spacing, including SpeedyCRM/Staff Web labels.
- [x] Shared mobile/browser accessibility pass: navigation labels, language label, focus-visible states and mobile link overflow.

### Verification and rollout

- [x] Add WebAuthn/passkey credential model and Alembic migration; persist only credential ID/public key/counter/device metadata.
- [x] Add feature-gated WebAuthn registration, assertion, challenge expiry/replay protection, club/user scope and credential revoke endpoints.
- [x] Add `fido2==2.2.1` to application requirements and run full local suite after the package.
- [x] Add browser-native WebAuthn UI for profile device registration and revoke.
- [x] Run authenticated hardware/browser WebAuthn smoke on staging: iOS Safari 17.6.1 registration succeeded and device revoke was confirmed.
- [x] Restore approved staging owner Email OTP fixture and confirm API restart/health after enabling the staging-only mail test helper.
- [x] Add and deploy staff Profile route so passkey controls are reachable from the authenticated navigation.
- [x] Fix and deploy WebAuthn nested-router prefix; verify `/auth/webauthn/credentials` auth gate.
- [x] Fix and deploy binary WebAuthn options serialization after staging log review.
- [x] Deploy Safari-compatible WebAuthn algorithm/user-verification options.
- [x] Deploy non-persisting authenticated passkey diagnostic page.
- [x] Fix base64url padding error found by Safari diagnostic page.
- [ ] Add tests for every remaining legacy operation: refunds, payment-method changes, receipt delivery and invitations.
- [x] Staging unauthenticated/auth-gating smoke after latest settings build; temporary flags remained disabled and no test data was created.
- [x] Verify staging rebuild and migration upgrade after current changes; staging is at `a1b2c3d4e5f6`.
- [ ] Verify migration downgrade/upgrade in a disposable separate staging test DB.
- [ ] Verify backup artifact and restore in a separate test DB.
- [ ] Human diff review, production backup approval, rollback image/commit review.
- [ ] Final Telegram regression smoke.
- [ ] Explicit approval before merge to `master`.

## Standard for every new Web mutation

1. Resolve actor from Web `AuthContext`.
2. Check owner/staff permission and feature flag.
3. Require CSRF for POST/PATCH/DELETE.
4. Take club/user/student/order scope from session, never trusted payload.
5. Validate an explicit payload allowlist.
6. Use row lock/transaction for money, stock, student, order and settings writes.
7. Require Redis/DB idempotency key for retries.
8. Write a structured audit event without secrets/raw payload.
9. Add targeted tests and run the full suite.
10. Run isolated staging smoke and record cleanup/status in `MIGRATION_PROGRESS.md`.

## Safe continuation commands

## Tomorrow recovery checklist

- [ ] Confirm `aaaa` and branch `web-migration/phase-0-auth`; do not checkout `master`.
- [ ] Confirm only staging target: `/root/speedycrm-staging`, compose project `speedycrm-staging`.
- [ ] Do not touch `/root/github.io-test`, `/root/alter`, `gym_db`, production/live or ALTER containers.
- [ ] Open SSH tunnel and separately verify `health`, `ready`, `docker compose ... ps`.
- [ ] Rebuild only with `docker compose -p speedycrm-staging -f docker-compose.staging.yml up -d --build`.
- [ ] Verify local `node --check static/web/components.js`, `git diff --check`, `.\venv\Scripts\python.exe -m pytest -q`.
- [ ] Audit Russian localization across all existing Web pages and states.
- [ ] Build Telegram-to-Web page/operation matrix; mark missing pages before adding new styling.
- [ ] Audit every button and form in admin, club settings, schedulers, notifications, broadcast, turnstile, clients, subscriptions, freezes, sales, cash and audit.
- [ ] Record each result and update all three progress/report files before committing.

## 2026-08-20 checkpoint — navigation visual package

- [x] Replace inline details menu with a left off-canvas drawer.
- [x] Include dynamically-added Profile/Broadcast/Menu/Schedulers/Turnstile links in the drawer.
- [x] Add backdrop, Escape, close button and close-on-navigation behavior.
- [x] Keep language and logout readable in the drawer footer.
- [x] Run `node --check`, `git diff --check` and full suite (`491 passed`).
- [ ] Deploy only to isolated staging and complete authenticated desktop/mobile visual sweep.

```powershell
cd C:\Users\79615\PycharmProjects\aaaa
git status --short --branch
git log -8 --oneline
py -m pytest -q
git diff --check
```

For staging, use only `/root/speedycrm-staging` and the SSH tunnel. Never print or paste tokens, cookies, `init_data`, payment keys or mail keys.
