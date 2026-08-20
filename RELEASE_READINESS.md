# SpeedyCRM Web release readiness

Branch: `web-migration/phase-0-auth`

## Current verified state

- Local full suite: `433 passed`.
- `git diff --check`: passed.
- Work is isolated from `master`.
- Telegram routes remain in place.
- Web session, CSRF, club isolation, read-only contracts, and feature flags are covered.
- Production server was not changed; isolated staging is running separately on `127.0.0.1:18000`.
- Controlled staging client fixture cycle completed and cleaned up; `/ready` returned 200 after restart.
- Native Email OTP was verified in isolated staging through Yandex Postbox; production flags remain disabled.

## Safe for staging preparation

- Review the diff and commit only after human review.
- Deploy to an isolated staging environment with test data.
- Run `PHASE1_BROWSER_SCENARIOS.md` manually.
- Keep all Web mutation flags disabled.
- Verify health, ready, logs, cookies, logout, and cross-club denial.

## Not safe for production yet

- No authorized real-browser smoke has been completed in staging: separate staging Telegram bot/test account is still required.
- Native Web auth is not enabled yet; SMS provider/test route is required before removing Telegram as the primary entry.
- Native Email OTP is implemented and verified in isolated staging, but remains disabled for production until role/cross-club security tests, anonymized staging data review, and backup/rollback gates are complete.
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

## Backup and rollback plan

- Create a custom-format PostgreSQL dump with `scripts/backup-db.sh`; the script verifies it with `pg_restore --list` and applies retention under a lock.
- Optional cloud copy uses `scripts/backup-db-to-s3.sh` and verifies the object with `aws s3api head-object`.
- Restore validation must use a separately named test database and `scripts/restore_check.py`; never point it at live or ALTER databases.
- Rollback is by reverting the deployment commit/image on the migration branch and restoring the verified database backup only under an approved incident procedure.
