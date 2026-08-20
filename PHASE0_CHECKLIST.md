# Phase 0 checklist

Branch: `web-migration/phase-0-auth`

## Complete

- [x] Common `AuthContext` with user, club, actor type, role, permissions, and auth source.
- [x] Telegram `init_data` remains the existing provider.
- [x] One-time Telegram exchange creates a server-side Redis session.
- [x] HttpOnly/Secure/SameSite=Lax session cookie.
- [x] CSRF token and reusable mutation guard.
- [x] `/auth/login`, `/auth/telegram/exchange`, `/auth/me`, `/auth/logout`.
- [x] Owner, staff, invalid-auth, logout, CSRF, and cross-club tests.
- [x] Web Forecast read-only proof of concept.
- [x] Legacy Telegram routes unchanged.
- [x] Web route registry smoke coverage.

## Deliberately deferred

- [ ] Email/phone standalone login provider.
- [ ] Mobile bearer/refresh provider.
- [ ] Full migration of legacy HTML routes to Web auth.
- [ ] Money, inventory, payment, freeze purchase, pricing, and destructive mutations.
- [ ] Production/staging deployment.

## Verification

- Full local suite: `410 passed`.
- No production SSH write or deployment performed.
- No push to `master` performed.

## Next phase

Finish read-only parity where needed, then implement mutations only from `WEB_MUTATION_CONTRACTS.md`, one contract at a time, with feature flags and rollback notes.
