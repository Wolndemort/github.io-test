# Web mutation contracts

Branch: `web-migration/phase-0-auth`

## Selected next mutation

`PATCH /api/v1/staff/schedule`

Why: schedule editing is a non-money staff mutation and can be isolated to one club's `club_settings.disciplines[*].schedule`.

Required request context:

- Web `AuthContext`.
- `schedule_edit` permission for staff.
- CSRF header `X-CSRF-Token`.
- Club loaded from `AuthContext.club_id`; no client `club_id` accepted.

Required validation:

- discipline exists in the authenticated club;
- day is one of `mon`…`sun`;
- lesson time/duration and payload shape are validated;
- schedule is normalized through the existing schedule utility;
- lesson fields are allowlisted (`time`, `duration`, `coach`, `group`, `capacity`, `discipline`);
- only the selected discipline/day is changed;
- no payment, student, tariff, or unrelated settings fields are accepted.

Required safety:

- transaction with row lock on the club;
- idempotency key for retries;
- audit event with actor, club, discipline, day, and change summary;
- no raw request payload or secrets in logs;
- tests for permission, CSRF, cross-club, invalid day, invalid discipline, idempotent retry, and unchanged unrelated settings.
- feature flag `WEB_SCHEDULE_MUTATIONS_ENABLED=1`; default is disabled.

## Deferred mutations

- Cash entry: money mutation; requires stronger idempotency/concurrency coverage.
- Product sale: money and inventory mutation; deferred.
- Subscription/freeze purchase: payment and entitlement mutation; deferred.
- Discount/tariff changes: pricing mutation; deferred.
- Audit deletion: destructive mutation; deferred.

No mutation from the deferred list is exposed by the Web API.

## Candidate after Schedule

`POST /api/v1/client/students`

This is the next candidate non-money mutation. It may create a student only for the authenticated user and club.

Required controls:

- web `AuthContext` and CSRF;
- no `user_id`, `club_id`, parent IDs, balance, expiry, or payment fields accepted from payload;
- strict name/date/discipline validation and length limits;
- duplicate identity check inside the authenticated club;
- transaction and idempotency key;
- audit event with safe metadata only;
- tests for cross-club, parent spoofing, duplicate retry, invalid input, and rollback.

Do not expose this mutation until these tests exist and the existing Telegram create-student flow remains unchanged.

Feature flag: `WEB_CLIENT_STUDENT_MUTATIONS_ENABLED=1`; default is disabled.

Bind-phone rollout flag: `WEB_CLIENT_BIND_PHONE_ENABLED=1`; default is disabled.
