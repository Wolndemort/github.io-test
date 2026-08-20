# SpeedyCRM Web-native authentication contract

## Goal

Allow staff and clients to enter Web without Telegram. Telegram exchange remains available as a transition fallback until the native flow is proven.

## First provider: SMS OTP

The first native provider is phone-number OTP because existing client records already contain parent phone data. The provider is abstracted behind an SMS adapter; provider credentials are server-only and never returned to Web clients.

### Request OTP

`POST /auth/native/request`

```json
{"phone":"+79991234567","club_id":1}
```

Rules:

- feature flag `WEB_NATIVE_AUTH_ENABLED` must be explicitly enabled;
- normalize phone to E.164 before lookup;
- never reveal whether a phone exists;
- rate-limit by phone, IP, and club;
- store only a hash of the OTP in Redis with a short TTL;
- one active code per phone/club;
- audit request without storing the code;
- SMS provider failures return a generic response.

### Verify OTP

`POST /auth/native/verify`

```json
{"phone":"+79991234567","club_id":1,"code":"123456"}
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

An SMS provider and a test phone route are required before enabling the flag. No provider token belongs in the repository or chat.
