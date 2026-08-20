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
- [ ] Schedule editing UI and backend smoke.
- [x] Discipline configuration mutation with safe schedule block allowlist.
- [x] Camera/turnstile configuration controls without exposing device secrets.
- [x] Notification preference mutation for safe boolean flags.
- [x] Staff list UI, create/update backend, permissions editor and edit form.
- [x] Client profile name editing; staff profile/user admin extensions remain.
- [x] Discount assignment list/removal backend; UI selector polish remains.
- [x] Product sale UI with scoped product, buyer and discount pickers.
- [x] Subscription/freeze UI with scoped tariff/student selectors.
- [ ] Online payment provider integration smoke with mocked provider; then approved real staging test.
- [ ] Webhook integration tests for success, wrong amount, wrong metadata, duplicate and provider retry.
- [ ] Any remaining legacy operations: refunds, payment method changes, receipt delivery, audit search/delete policy, invitations, phone/email binding.

### Frontend

- [ ] Replace remaining temporary ID inputs with authenticated selectors and data tables.
- [x] Add shared loading/aria-busy state; mutation forms retain explicit error/success messages.
- [x] Keep async staff summaries separate from mutation mount points so operation buttons survive loading.
- [x] Add confirmation guard for money, archive, reversal, cancellation and freeze/sale mutations.
- [ ] Full EN/RU translations; current selector changes language state but is not a complete translation system.
- [ ] Final shared design polish and spacing, including SpeedyCRM/Staff Web labels.
- [ ] Mobile/browser accessibility pass.

### Verification and rollout

- [ ] Add tests for every new endpoint: permission, CSRF, feature flag, club scope, validation, idempotency, transaction/lock, audit.
- [ ] Run full local suite after each package.
- [x] Staging unauthenticated/auth-gating smoke after latest settings build; temporary flags remained disabled and no test data was created.
- [ ] Verify staging migration refresh and downgrade/upgrade after current changes.
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

```powershell
cd C:\Users\79615\PycharmProjects\aaaa
git status --short --branch
git log -8 --oneline
py -m pytest -q
git diff --check
```

For staging, use only `/root/speedycrm-staging` and the SSH tunnel. Never print or paste tokens, cookies, `init_data`, payment keys or mail keys.
