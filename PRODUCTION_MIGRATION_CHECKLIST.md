# SpeedyCRM Web production migration checklist

Branch: `web-migration/phase-0-auth`

## Completed

- [x] Web session, CSRF, logout/revocation and club scope.
- [x] Telegram WebApp fallback remains available.
- [x] Native Email OTP verified in isolated staging through Yandex Postbox.
- [x] Staff browser smoke and controlled staging client fixture cycle.
- [x] Cross-club and read-only route contract coverage.
- [x] Reversible nullable email migration contract.
- [x] Backup script verification contract and isolated restore-check procedure.

## Required before production

- [ ] Human review of `git diff master...HEAD` and changed-file list.
- [x] Staging custom-format backup created and verified with `pg_restore --list`.
- [x] Staging restore check completed in separately named `crm_restore_check_20260820`, then test DB removed.
- [ ] Fresh production backup, with artifact listing and retention check — requires explicit production approval.
- [x] Confirm migration upgrade/downgrade compatibility against a staging DB refreshed from current live snapshot; email revision downgraded and upgraded successfully.
- [ ] Final Telegram regression smoke on the unchanged live bot path.
- [ ] Decide native Email OTP rollout percentage and rollback trigger; keep production flags disabled until approved.
- [ ] Final UI spacing/translation pass.
- [ ] Explicit approval to merge into `master` and allow GitHub Actions production deploy.

## Safety rules

- Never run restore against `/root/github.io-test` or `/root/alter`.
- Never copy live bot tokens, payment secrets, mail keys or `init_data` into git/chat/logs.
- All staging work uses `/root/speedycrm-staging` and its separate DB/Redis/network.
