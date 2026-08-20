# Phase 1 — Browser Web rollout checklist

Branch: `web-migration/phase-0-auth`

## Ready locally

- [x] Web session exchange and `/auth/me`.
- [x] CSRF-protected logout.
- [x] Forecast read-only API and browser POC.
- [x] Shared SpeedyCRM shell, navigation, tables, breadcrumbs, loading/error helpers.
- [x] Staff/client read-only routes with club/user isolation.
- [x] Feature flags for current Web mutations.
- [x] SpeedyCRM branding and shared browser components.

## Browser smoke to perform before staging

- [x] Open Web entry from a real Telegram exchange.
- [ ] Confirm HttpOnly session cookie and readable CSRF cookie attributes.
- [ ] Confirm refresh keeps the session and logout revokes it.
- [x] Open Forecast, Revenue, Students, and staff detail/navigation pages in staging Telegram WebApp.
- [ ] Verify a user cannot read another club by changing IDs in URLs.
- [ ] Verify expired/invalid session returns 401/redirect behavior.
- [ ] Verify error and empty states render without leaking backend details.
- [x] Automated contract smoke covers assets, auth, branding, CSRF wiring, and default-disabled mutations.

## Staging gate

- [x] Create a separate staging deployment or approved isolated environment.
- [ ] Run migrations only after backup and rollback plan review.
- [ ] Keep all Web mutation flags disabled initially.
- [ ] Compare Telegram behavior before/after the deployment.
- [ ] Enable one flag at a time only after explicit approval.

## Production gate

- [x] Full tests and unauthenticated staging smoke checks green.
- [ ] Production backup verified.
- [ ] GitHub Actions run reviewed manually.
- [ ] Rollback command and previous image identified.
- [ ] No direct push to `master` from this branch.
