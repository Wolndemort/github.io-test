# SpeedyCRM Web release readiness

Branch: `web-migration/phase-0-auth`

## Current verified state

- Local full suite: `411 passed`.
- `git diff --check`: passed.
- Work is isolated from `master`.
- Telegram routes remain in place.
- Web session, CSRF, club isolation, read-only contracts, and feature flags are covered.
- Production server was not changed; isolated staging is running separately on `127.0.0.1:18000`.

## Safe for staging preparation

- Review the diff and commit only after human review.
- Deploy to an isolated staging environment with test data.
- Run `PHASE1_BROWSER_SCENARIOS.md` manually.
- Keep all Web mutation flags disabled.
- Verify health, ready, logs, cookies, logout, and cross-club denial.

## Not safe for production yet

- No authorized real-browser smoke has been completed in staging: separate staging Telegram bot/test account is still required.
- The visual UI is still a functional shell/POC, not final product polish.
- Money, inventory, payment, purchase, freeze, pricing, and audit-delete mutations are deferred.
- The current branch is committed but has not been merged or pushed to `master`.
- Do not push to `master`; its workflow deploys production automatically.

## Required approval gates

1. Human review of the branch diff.
2. Approved staging environment.
3. Browser smoke and Telegram regression checks.
4. Backup and rollback verification.
5. Separate explicit approval for production deployment.
