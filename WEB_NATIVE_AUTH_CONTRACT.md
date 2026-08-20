# SpeedyCRM Web-native authentication contract

## Goal

Allow staff and clients to enter Web without Telegram. Telegram exchange remains available as a transition fallback until the native flow is proven.

## First provider: Email OTP

The first native provider is email OTP. It is inexpensive for staging and avoids SMS-provider dependency. Email delivery is abstracted behind a mail adapter; SMTP credentials are server-only and never returned to Web clients.

### Request OTP

`POST /auth/native/request`

```json
{"email":"user@example.com","club_id":1}
```

Rules:

- feature flag `WEB_NATIVE_AUTH_ENABLED` must be explicitly enabled;
- normalize and lowercase the email before lookup;
- never reveal whether a phone exists;
- rate-limit by email, IP, and club;
- store only a hash of the OTP in Redis with a short TTL;
- one active code per phone/club;
- audit request without storing the code;
- SMS provider failures return a generic response.

### Verify OTP

`POST /auth/native/verify`

```json
{"email":"user@example.com","club_id":1,"code":"123456"}
```

Rules:

- constant-time hash comparison and bounded attempts;
- consume the code atomically on success;
- resolve the actor inside the selected club only;
- create the existing Redis `AuthContext` session and CSRF cookie;
- never accept `user_id`, role, permissions, or club scope from the client;
- audit successful and failed verification without logging the code.

## Rollout

`WEB_NATIVE_AUTH_ENABLED=0` by default. Enable only in isolated staging with a real SMS test route, then one approved club at a time. Telegram routes and bot behavior remain unchanged.

## Current blocker

A verified email field and an SMTP/mail provider are required before enabling the flag. The adapter follows the existing ALTER reference naming (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_USE_TLS`), but credentials are separate per deployment and are never copied from ALTER or placed in the repository/chat.
