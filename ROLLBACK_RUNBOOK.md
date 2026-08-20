# SpeedyCRM Web rollback runbook

Current migration branch: `web-migration/phase-0-auth`

Reviewed base commit (`master`): `57ef297fa156cd28ca59b3c8600c8b167ecad142`

Current staging commit: `eafffef12189665c0bbda32274704fe7ff82aa89`

## Guarded rollback procedure

1. Stop the rollout and preserve deploy logs and the pre-deploy database dump.
2. On the production host, verify the target commit/image and obtain explicit incident approval.
3. Restore the previous application image/commit using the production deployment procedure; do not run commands against staging paths.
4. Run `alembic heads`, `/health`, `/ready`, and the production smoke check.
5. Restore the database only if the incident requires it, using the verified pre-deploy backup and an approved maintenance window.
6. Confirm Telegram bot behavior and monitor logs before reopening traffic.

The GitHub Actions workflow deploys only `master`/`main` (or an explicitly invoked workflow), not this migration branch. No rollback command is executed by this document.
