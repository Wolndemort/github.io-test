# Phase 1 browser scenarios

Use only in an isolated/staging environment. Do not paste real `init_data`, cookies, tokens, or secrets into tickets/logs.

## 1. Telegram exchange

1. Open the Web entry from the Telegram client.
2. Perform one-time `/auth/telegram/exchange`.
3. Confirm response does not contain `init_data`.
4. Confirm session cookie is HttpOnly/Secure/SameSite=Lax.
5. Confirm CSRF cookie is Secure/SameSite=Lax and not HttpOnly.

## 2. Session lifecycle

1. Open `/auth/me` after exchange.
2. Refresh the browser and repeat `/auth/me`.
3. Click shared Logout.
4. Confirm `/auth/me` becomes 401 after logout.
5. Confirm a previous session cookie cannot be reused.

## 3. Staff read-only flow

Open, with an actor allowed in the selected club:

- `/staff/overview`
- `/staff/forecast`
- `/staff/revenue`
- `/staff/students`
- `/staff/cash`
- `/staff/sales`
- `/staff/audit/search`
- `/staff/schedule`
- `/staff/products`
- `/staff/discounts`
- `/staff/tariffs`
- `/staff/checkin`
- `/staff/freeze`

Confirm loading, empty, error, table, mobile, and permission-denied states.

## 4. Client read-only flow

Open:

- `/client/hub`
- `/client/cabinet`
- `/client/subscriptions`
- `/client/purchases`
- `/client/history`
- `/client/freeze`
- `/client/schedule`
- `/client/products`
- `/client/discounts`
- `/client/tariffs`
- `/client/club`
- `/client/me`
- `/client/legal`

Confirm all data belongs to the session user and club.

## 5. Mutation safety

- With flags unset, Schedule, bind-phone, and student creation must return `404 feature_disabled`.
- With a flag enabled in staging, missing/invalid CSRF must return `403`.
- Replay the same idempotency key and confirm no duplicate write.
- Do not enable money, inventory, payment, freeze purchase, or audit-delete mutations.
