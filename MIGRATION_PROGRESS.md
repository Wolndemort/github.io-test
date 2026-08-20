# Web migration progress

## Current state

- Project: `C:\Users\79615\PycharmProjects\aaaa`
- Branch: `web-migration/phase-0-auth`
- Base branch: `master`
- Current phase: Phase 0 — audit and common authentication context
- Production deploy: not authorized; do not push this branch to `master`
- Last completed stage: audited the current Telegram authentication and club context entry points

## Server reference

- SSH command: `ssh -i C:\Users\79615\.ssh\alter_agent root@77.73.131.175`
- Production project: `/root/github.io-test`
- API container: `gym_crm_api`
- Database container: `gym_db`
- Redis container: `githubio-test-redis-1`

Do not copy private keys, `.env` values, tokens, Telegram `init_data`, or database dumps into this file or Git.

## Goal

Add browser authentication beside the existing Telegram authentication without breaking Telegram users, routes, payments, permissions, or production behavior.

## Stages

1. [x] Create isolated branch and continuation journal.
2. [x] Audit Telegram `init_data` verification, actor lookup, club context, permissions, and routes trusting query parameters.
3. [x] Define the common `AuthContext` and provider boundary.
4. [x] Add web-session authentication and `/auth/me`, `/auth/logout`, `/auth/telegram/exchange` without removing Telegram auth.
5. [x] Add authentication, permission, and cross-club isolation tests.
6. [x] Run local tests and smoke checks.
7. [ ] Review diff and decide separately whether anything may be deployed.

## Working rules

- Work only on `web-migration/phase-0-auth` until explicitly changed.
- Do not use the production server for development changes.
- Do not run `git push` or production deployment automatically.
- After each meaningful change, update this file with completed work, remaining work, and verification.

## Resume checklist

1. `git switch web-migration/phase-0-auth`
2. Read this file and inspect `git status`.
3. Continue from the first unchecked stage.
4. Run tests before considering a commit or deployment.

## Log

### 2026-08-20 — Stage 1

- Created branch `web-migration/phase-0-auth` from the clean `master` state.
- Added this continuation journal in the project root.
- No application code, database, server, or production configuration changed.
- Remaining: audit current authentication and authorization flow.

### 2026-08-20 — Stage 2

- Confirmed Telegram signatures and `auth_date` age are checked in `admin_module/webapp_verify.py`.
- Confirmed staff authorization is currently implemented in `admin_module/utils.py` through `verify_webapp_staff` and `staff_can`.
- Confirmed the application already has Starlette `SessionMiddleware`, but it is not yet a web actor/session provider.
- Found legacy WebApp routes and payloads that carry `init_data` and/or `club_id`; these remain compatibility routes and must not be rewritten in Phase 0.
- Found `get_club_id_from_host` falls back to query-string `club_id`; the new web session must not use that fallback for authorization.
- Remaining: define and implement a separate common auth context/provider layer, then add tests before touching existing route behavior.

### 2026-08-20 — Stage 3

- Added `auth/context.py` with the canonical `AuthContext`.
- Added Redis-backed web sessions with random server-side IDs, HttpOnly/Secure/SameSite=Lax cookie, TTL refresh, and logout revocation.
- Added `/auth/telegram/exchange`, `/auth/me`, and `/auth/logout` in a separate router.
- Telegram exchange validates `init_data` against the selected club, then resolves owner/super-admin/staff permissions from the database.
- Existing Telegram WebApp routes and legacy query/payload contracts were not changed.
- Not verified yet: imports, tests, endpoint behavior, CSRF strategy, and cross-club tests.
- Remaining: add tests, run local checks, then review security gaps before any deployment.

### 2026-08-20 — Stage 4

- `python -m compileall -q auth main.py` passed.
- `git diff --check` passed.
- Existing auth/security tests passed: `24 passed` (`test_hmac.py`, `test_staff_permissions.py`, `test_security_webapp.py`).
- No production commands, SSH writes, pushes, migrations, or database changes were performed.
- Remaining: add dedicated web-session and exchange tests; add CSRF protection for state-changing web endpoints before exposing mutations; integrate `AuthContext` into a new read-only Web proof of concept.

### 2026-08-20 — Stage 5

- Added `tests/test_web_auth_session.py`: server-side storage, context round-trip, TTL refresh, and revocation tests.
- Added `tests/test_web_auth_routes.py`: owner Telegram exchange, invalid Telegram auth rejection, unauthenticated `/auth/me`, and logout tests.
- New auth tests pass: `6 passed`.
- Existing auth/security tests remain covered; no production or server changes made.
- Remaining: run the combined suite, then add CSRF protection before introducing state-changing Web endpoints.

### 2026-08-20 — Stage 6

- Added a per-session CSRF secret stored server-side in Redis.
- Telegram exchange now issues a separate readable CSRF cookie; the session cookie remains HttpOnly.
- Added `X-CSRF-Token` validation for logout and deletion of both cookies on successful logout.
- Added tests for matching/missing CSRF headers and invalid logout attempts.
- New auth tests pass: `8 passed`.
- Remaining: run the combined regression suite and review the auth diff; apply the same CSRF dependency to future Web mutations.

### 2026-08-20 — Stage 7

- Full local regression suite passed: `275 passed` before the Forecast endpoint addition.
- Added read-only Web Forecast access endpoint: `GET /api/v1/staff/forecast/access`.
- Endpoint requires the new web session and `forecast_view` for staff; owner access is allowed.
- Added three endpoint tests for allowed staff, denied staff, and unauthenticated access; `3 passed`.
- Legacy `/forecast` and all Telegram routes remain unchanged.
- Remaining: replace the access proof with a real read-only Forecast JSON contract, add cross-club tests, then build the Web UI against that contract.

### 2026-08-20 — Stage 8

- Added `GET /api/v1/staff/forecast/data` using the existing analytics services and the authenticated session's `club_id`.
- Added independent date ranges for expiry, revenue, and visits; response is explicitly read-only JSON.
- Database queries are scoped by the authenticated actor's club, never by a client-supplied `club_id`.
- Added a JSON contract test covering club scoping, student serialization, read-only response, and absence of secrets.
- Forecast tests pass: `4 passed`; compile and diff checks pass.
- Remaining: add route-level cross-club tests with a database/session fixture, then build the browser UI against this contract.

### 2026-08-20 — Stage 9

- Added separate browser route `GET /staff/forecast`; legacy Telegram `/forecast` remains untouched.
- The browser route requires the web session and `forecast_view`, then loads read-only data from `/api/v1/staff/forecast/data`.
- Added page tests for authorized access, permission denial, and separation from the Telegram route.
- Web Forecast tests pass: `7 passed`.
- This is intentionally a functional POC shell; premium visual components and full chart rendering remain for the next UI stage.
- Remaining: add database-backed route-level cross-club tests and replace the POC shell with the Forecast visual system.

### 2026-08-20 — Stage 10

- Added route-level cross-club isolation test for Forecast data.
- The test verifies every student/visit/payment/cart/cash query binds the authenticated actor's `club_id`.
- Forecast access tests now pass: `5 passed`.
- Confirmed the shared-design direction: future Web pages should consume common layout/tokens/components rather than define page-specific styles.
- Remaining: run the full suite, then create the shared Web shell/design tokens and upgrade the Forecast POC UI.

### 2026-08-20 — Stage 11

- Added shared Web design tokens and responsive primitives in `static/web/design.css`.
- Updated `/staff/forecast` to use the shared shell, monochrome palette, hero block, cards, spacing, and mobile layout.
- Kept the page read-only and connected to the existing Forecast JSON endpoint.
- Added design-system tests; UI tests pass: `5 passed`.
- Remaining: run the full suite, then continue extracting reusable navigation/loading/error components before migrating another page.

### 2026-08-20 — Stage 12

- Added shared browser components in `static/web/components.js`: navigation, loading, error, and JSON client helpers.
- Updated Forecast POC to consume the shared navigation/error/client helpers.
- Added the next read-only Revenue API route at `GET /api/v1/staff/revenue/data`, protected by `analytics_view` and scoped to the authenticated club.
- Added Revenue permission and cross-club query tests.
- Component and Revenue tests pass: `7 passed`.
- Remaining: complete the Revenue Web page using the shared shell, then run the full suite.

### 2026-08-20 — Stage 13

- Added browser page `GET /staff/revenue` using the shared Web shell and components.
- Added shared loading/error handling and read-only Revenue KPI cards.
- Page requires `analytics_view`; legacy Telegram revenue routes remain unchanged.
- Added Revenue page tests; targeted tests pass.
- Remaining: migrate the next read-only staff section, then continue extracting shared navigation and page states.

Verification for Stage 13:

- Full local suite: `292 passed`.
- Compile check and `git diff --check` passed.
- No push, SSH write, migration, database modification, or production deployment performed.

### 2026-08-20 — Stage 14

- Added read-only Students API: `GET /api/v1/staff/students/data`.
- Added browser page `GET /staff/students` using the shared Web shell and components.
- Data is filtered by the authenticated session's club and requires `analytics_view` for staff.
- Added Students API/page tests.
- Remaining: improve the Students table with pagination/search while preserving the shared shell, then migrate the next read-only staff section.

### 2026-08-20 — Stage 15

- Added bounded server-side Students search (`q`) and pagination (`limit`, `offset`).
- Search and pagination are applied after the authenticated club filter, not in the browser only.
- Added response pagination metadata and test coverage for query/limit/offset.
- Remaining: expose the search/pagination controls in the Students page, then migrate the next read-only section.

Verification for Stage 15:

- Students API/page tests pass: `3 passed`.
- Full local suite remains green: `294 passed`.
- Search control now uses the shared Web JSON client and server-side query parameters.
- Remaining: migrate the next read-only staff section.

### 2026-08-20 — Stage 16

- Added read-only Overview API: `GET /api/v1/staff/overview/data`.
- Added browser page `GET /staff/overview` using the shared shell and components.
- Overview uses existing analytics calculations and scopes students/visits to the authenticated club.
- Added Overview permission and club-isolation tests.
- Remaining: migrate the next read-only staff section and keep extending shared page components.

### 2026-08-20 — Stage 17

- Batch-added read-only Cash API: `GET /api/v1/staff/cash/data`.
- Batch-added read-only Sales API: `GET /api/v1/staff/sales/data`.
- Cash requires `cash_view`; Sales requires `analytics_view`; both use the authenticated club only.
- Added tests for permissions, club scoping, totals, and read-only contracts.
- Full suite verification pending after this batch.

Verification for Stage 17:

- Targeted Cash/Sales tests: `3 passed`.
- Full local suite: `300 passed`.
- Compile and diff checks passed.
- No production/server changes or pushes performed.
- Remaining: add shared browser pages for Cash and Sales, then continue with the next migration batch.

### 2026-08-20 — Stage 18

- Added browser pages `GET /staff/cash` and `GET /staff/sales`.
- Both pages use the shared shell/components and remain read-only.
- Cash page requires `cash_view`; Sales page requires `analytics_view`.
- Added page tests for shared API wiring and permission denial.
- Remaining: migrate the next batch of read-only pages, then begin carefully scoped mutations only after CSRF/API contracts are complete.

### 2026-08-20 — Stage 19

- Added read-only Audit API: `GET /api/v1/staff/audit/data` with club scope and pagination.
- Added browser page `GET /staff/audit` using the shared shell/components.
- Audit requires `analytics_view`; no audit mutation or delete route was exposed.
- Added Audit tests for permission and club isolation.
- Schedule remains next: its source is club settings and needs a dedicated read-only contract before implementation.

### 2026-08-20 — Stage 20

- Added read-only Schedule API: `GET /api/v1/staff/schedule/data`.
- Added browser page `GET /staff/schedule` using club `disciplines[*].schedule` settings.
- Schedule requires `schedule_view`; data is loaded only from the authenticated club.
- Added Schedule tests for permission and club-scoped settings.
- Remaining: cover other club-settings sections (tariffs/products/discounts) through dedicated contracts; do not expose raw settings wholesale.

### 2026-08-20 — Stage 21

- Added read-only catalog APIs:
  - `GET /api/v1/staff/catalog/products`
  - `GET /api/v1/staff/catalog/discounts`
  - `GET /api/v1/staff/catalog/tariffs`
- Products, discounts, and tariffs expose dedicated safe fields only; raw club settings are not returned.
- Added permissions and club-scope tests.
- Remaining: add browser pages for catalog sections, then handle client cabinet and remaining WebApp areas.

### 2026-08-20 — Stage 22

- Added browser pages:
  - `GET /staff/products`
  - `GET /staff/discounts`
  - `GET /staff/tariffs`
- All three use a shared catalog page helper, common shell/components, dedicated API endpoints, and permissions.
- Added catalog page tests for API wiring and denied access.
- Remaining: begin Client Cabinet migration and then continue remaining staff WebApp sections.

### 2026-08-20 — Stage 23

- Added Client Cabinet API: `GET /api/v1/client/cabinet/data`.
- Added browser page `GET /client/cabinet` using the shared Web shell/components.
- Client data is filtered by both authenticated `club_id` and authenticated `user_id`; neither is accepted from query parameters.
- Added Client Cabinet tests for identity and club isolation.
- Remaining: migrate remaining client pages (history, freeze, subscription) and staff mutations only after their contracts are designed.

### 2026-08-20 — Stage 24

- Added Client History API/page:
  - `GET /api/v1/client/history/data`
  - `GET /client/history`
- Added Client Freeze status API/page:
  - `GET /api/v1/client/freeze/data`
  - `GET /client/freeze`
- Both use authenticated user and club scope; both are read-only.
- Added tests for client identity isolation, shared API wiring, and read-only responses.
- Remaining: migrate client subscription/status view, then review all read-only coverage before mutations.

### 2026-08-20 — Stage 25

- Added Client Subscription API/page:
  - `GET /api/v1/client/subscriptions/data`
  - `GET /client/subscriptions`
- Subscription status is derived from authenticated user's own students and includes discipline, expiry, balance, and active state.
- Added identity/club isolation and page wiring tests.
- Remaining: audit the complete read-only migration matrix, then plan the first safe mutation contract with CSRF/idempotency.

### 2026-08-20 — Stage 26

- Added `WEB_MIGRATION_MATRIX.md` in the project root.
- Recorded all currently implemented read-only APIs/pages, permissions, scopes, and remaining mutation areas.
- Confirmed 16 read-only areas have a Web API/page contract; final visual polish remains intentionally deferred.
- Defined the mutation gate: AuthContext, backend permission, CSRF, server-side recalculation, idempotency/concurrency tests, and rollback safety.
- Full suite at this stage: `318 passed`.
- Next: choose the first low-risk mutation contract, most likely a client-safe action, and implement it only with the mutation gate tests.

### 2026-08-20 — Stage 27

- Extracted reusable `require_csrf(redis, request)` for all future Web POST/PATCH/DELETE handlers.
- Added mutation-gate tests for missing and matching CSRF tokens.
- Confirmed logout is the first existing Web mutation and remains CSRF-protected.
- Updated `WEB_MIGRATION_MATRIX.md` with the reusable guard.
- Remaining: adapt the existing client bind-phone flow into a Web mutation with server-side identity/club checks and idempotency before exposing it.

### 2026-08-20 — Stage 28

- Added first Web data mutation: `POST /api/v1/client/bind-phone`.
- Mutation requires web session and CSRF, normalizes phone input, derives user/club from AuthContext, and avoids duplicate `StudentParent` links.
- Added tests for successful binding and CSRF rejection.
- Remaining: add rate limiting/audit telemetry to bind-phone, then design the next money-affecting mutation separately.

### 2026-08-20 — Stage 29

- Added Redis rate limiting to Web bind-phone: maximum 3 attempts per user/club per 60 seconds.
- Added audit telemetry for successful and rate-limited attempts; phone data is reduced to a four-digit tail.
- Added rate-limit test coverage.
- Remaining: design the next mutation only after reviewing this contract; no payment or inventory mutation is enabled yet.

Verification for Stage 29:

- Bind-phone targeted tests: `3 passed`.
- Full local suite: `323 passed`.
- Compile and `git diff --check` passed.
- No push, SSH write, migration, database modification, or production deployment performed.

## Next steps after Stage 29

### 2026-08-20 — Stage 60

- Added production-safe `WEB_CLIENT_BIND_PHONE_ENABLED` flag; default is disabled with `404 feature_disabled`.
- Updated bind-phone tests to enable the flag explicitly and added default-disabled coverage.
- Both Client Web data mutations now require explicit feature flags in addition to CSRF and identity checks.
- Remaining: continue legacy read-only parity; no money mutation is enabled.

### 2026-08-20 — Stage 61

- Synchronized `WEB_MUTATION_CONTRACTS.md` with both client rollout flags.
- Added regression coverage proving both client mutation flags default to disabled in source and are documented.
- Full local suite before this small documentation/test addition: `366 passed`.
- Remaining: continue the next read-only migration block; do not enable client mutations in production.

### 2026-08-20 — Stage 62

- Added read-only Limits settings API/page:
  - `GET /api/v1/staff/settings/limits`
  - `GET /staff/settings/limits`
- Limits use an explicit allowlist and exclude unknown/secrets fields.
- Added club-scope, redaction, and page wiring tests.
- Remaining: continue the next read-only settings/legacy block; no settings mutations enabled.

### 2026-08-20 — Stage 63

- Added read-only Branding settings API/page:
  - `GET /api/v1/staff/settings/branding`
  - `GET /staff/settings/branding`
- Branding response allowlists club name, logo URL, and theme only.
- Added club-scope, redaction, and page wiring tests.
- Remaining: continue read-only parity; branding/settings mutations remain disabled.

### 2026-08-20 — Stage 64

- Added read-only Integrations status API/page:
  - `GET /api/v1/staff/settings/integrations`
  - `GET /staff/settings/integrations`
- Returns provider status booleans only; tokens, payment keys, and secrets are never returned.
- Added status redaction and page wiring tests.
- Remaining: continue read-only parity; integration settings remain non-mutating.

### 2026-08-20 — Stage 65

- Added Staff Product detail API/page:
  - `GET /api/v1/staff/catalog/products/{product_id}`
  - `GET /staff/products/{product_id}`
- Product detail is restricted to active products in the authenticated club and exposes no mutation.
- Added product scope and permission tests.
- Remaining: continue catalog/detail parity; inventory mutations remain deferred.

### 2026-08-20 — Stage 66

- Added catalog detail APIs/pages for active Discount and Tariff records.
- Discount detail is club-scoped and requires `analytics_view`; tariff detail is discipline-scoped and requires `tariffs_manage`.
- Added detail contract tests; no pricing mutation exposed.
- Remaining: continue catalog/detail parity; pricing and inventory mutations remain deferred.

### 2026-08-20 — Stage 67

- Added Staff Sale detail API: `GET /api/v1/staff/sales/{order_id}`.
- Detail requires `analytics_view`, current club scope, and confirmed payment status.
- Provider IDs/secrets and mutations are not exposed.
- Added sale scope and permission tests.
- Remaining: add the browser detail page and continue read-only detail parity.

### 2026-08-20 — Stage 68

- Added Staff Sale detail page: `GET /staff/sales/{order_id}`.
- Page uses the shared shell/components and the club-scoped sale API.
- Added page wiring and permission tests.
- Remaining: continue the next 3–4 detail/read-only blocks; payment mutations remain disabled.

### 2026-08-20 — Stage 69

- Added Audit event detail API/page:
  - `GET /api/v1/staff/audit/{entry_id}`
  - `GET /staff/audit/{entry_id}`
- Detail is club-scoped, read-only, and redacts secret-like payload keys.
- Added audit detail scope, redaction, and permission tests.
- Remaining: continue read-only detail parity; audit deletion remains deferred.

### 2026-08-20 — Stage 70

- Added server-side Audit filters for `event` and `actor_role`.
- Filters are applied after the authenticated club scope and returned in response metadata.
- Added filter contract coverage; audit deletion remains disabled.
- Remaining: continue read-only parity and keep destructive audit operations deferred.

### 2026-08-20 — Stage 71

- Added Audit search page: `GET /staff/audit/search`.
- Page exposes event and actor-role filters and calls the server-side filtered Audit API.
- Added page wiring and permission tests.
- Remaining: continue read-only parity; audit deletion remains deferred.

### 2026-08-20 — Stage 72

- Added Client Schedule API/page:
  - `GET /api/v1/client/schedule/data`
  - `GET /client/schedule`
- Schedule is extracted from a safe disciplines/schedule allowlist and scoped to the authenticated club.
- Added club-scope and page wiring tests; client schedule mutation is not exposed.
- Remaining: continue client read-only parity and keep schedule mutations staff-only/flagged.

### 2026-08-20 — Stage 73

- Added Client Products API/page:
  - `GET /api/v1/client/products/data`
  - `GET /client/products`
- Catalog is active-products-only, club-scoped, and excludes stock/inventory management fields.
- Added product scope and page wiring tests; product sale mutation is not exposed.
- Remaining: continue client read-only parity and keep product sales deferred.

### 2026-08-20 — Stage 74

- Added Client Discounts API/page:
  - `GET /api/v1/client/discounts/data`
  - `GET /client/discounts`
- Discounts are restricted to active assignments for the authenticated user and club.
- No manual discount application or pricing mutation is exposed.
- Added user/club scope and page wiring tests.
- Remaining: continue client read-only parity; pricing mutations remain deferred.

### 2026-08-20 — Stage 75

- Batch-added Client Tariffs, Notifications status, and Club info APIs/pages.
- All three use authenticated club scope and safe read-only responses.
- Added batch contract and page wiring tests.
- Remaining: continue in 3–4 block batches; pricing, notifications, and club settings mutations remain disabled.

### 2026-08-20 — Stage 76

- Batch-added Client summary APIs/pages for attendance, subscriptions, and purchases.
- All summaries use authenticated user/club scope and remain read-only.
- Added batch contract and page wiring tests.
- Remaining: continue 3–4 block batches; payment and entitlement mutations remain disabled.

### 2026-08-20 — Stage 77

- Batch-expanded Client navigation with all current read-only routes: schedule, products, discounts, tariffs, club, and three summaries.
- Added navigation coverage for the complete client read-only surface.
- No page-local menus or mutations added.
- Remaining: continue the next 3–4 migration blocks.

### 2026-08-20 — Stage 82

- Corrected Web UI branding from `ALTER` to `SpeedyCRM`.
- The separate ALTER project remains untouched; only newly created Web UI labels/titles were changed.
- Shared component behavior remains `AlterWeb.*` as a technical namespace and was not renamed.
- Remaining: continue migration using SpeedyCRM branding.

### 2026-08-20 — Stage 86

- Renamed the technical Web JS namespace from `AlterWeb` to `SpeedyCRMWeb`.
- Confirmed the separate ALTER project/reference files were not changed.
- Updated all generated Web UI calls and tests to the SpeedyCRM namespace.
- Remaining: continue Web migration under the correct SpeedyCRM branding.

### 2026-08-20 — Stage 87

- Added branding regression test for generated Web files.
- Test prevents accidental reintroduction of `ALTER` or `AlterWeb` into the SpeedyCRM Web layer.
- Reference handoff and separate ALTER project remain untouched.
- Remaining: continue migration under SpeedyCRM branding.

### 2026-08-20 — Stage 88

- Added `PHASE1_CHECKLIST.md` for browser smoke, staging gate, and production gate.
- Phase 0 remains local-only and complete; Phase 1 browser verification is explicitly not yet performed against production.
- All Web mutation flags remain disabled by default.
- Remaining: execute browser smoke in an approved isolated environment, not on the live server.

### 2026-08-20 — Stage 89

- Added local automated Phase 1 Web contract smoke tests for assets, auth, branding, CSRF wiring, and default-disabled mutations.
- No live browser or production server was contacted.
- Remaining: execute real browser smoke only in an approved isolated/staging environment.

### 2026-08-20 — Stage 90

- Added `PHASE1_BROWSER_SCENARIOS.md` with reproducible staging browser scenarios for auth, sessions, staff/client read-only flows, mobile states, cross-club checks, and mutation safety.
- Scenarios explicitly prohibit sharing real tokens/init data and prohibit production execution.
- Remaining: run these scenarios only after an isolated staging environment is approved.

### 2026-08-20 — Stage 91

- Added Web secret-hygiene tests for generated assets and HTML.
- Tests reject credential/logging patterns in shared assets and permanent `init_data=` links in new Web routes.
- Remaining: continue security review and execute browser scenarios only in approved staging.

### 2026-08-20 — Stage 93

- Synchronized Phase 0/Phase 1 checklists with current SpeedyCRM branding, browser components, feature flags, and test count.
- Current full local suite: `410 passed`.
- Real browser/staging checks remain pending because production is intentionally untouched.

### 2026-08-20 — Stage 94

- Added `RELEASE_READINESS.md` with verified state, staging preparation, production blockers, and approval gates.
- Full local suite remains `410 passed`; diff check passed.
- No commit, push, SSH write, or production deployment performed.
- Next required action: human diff review and approved isolated staging before any rollout.

### 2026-08-20 — Stage 95

- Created checkpoint commit `a5b6c15` on `web-migration/phase-0-auth`.
- Staged-file review found no `.env`, key, certificate, or backup files.
- Working tree is clean after the checkpoint.
- No push, merge, SSH write, or production deployment performed.
- Next: human review of commit, then approved isolated staging/browser smoke.
- Remaining: continue approved local work or run browser scenarios only in isolated staging.

Verification for Stage 92:

- Auth audit tests: `7 passed` after correcting the CSRF-valid fixture.
- Full suite final rerun pending.

### 2026-08-20 — Stage 92

- Added safe audit telemetry for Web Telegram exchange and logout.
- Auth telemetry excludes init data, cookies, tokens, and secrets.
- Added logout audit regression coverage.
- Remaining: continue security review and execute browser scenarios only in approved staging.

### 2026-08-20 — Stage 83

- Added Web route registry smoke tests covering main router registration, Phase 0 auth entrypoints, read-only entries, feature-flagged mutations, and preserved Telegram routes.
- Updated `PHASE0_CHECKLIST.md` with route registry coverage.
- Remaining: browser-level session smoke tests and UI/data contract review before staging.

Verification for Stage 83:

- Route registry targeted tests: `3 passed` after correcting the router ownership assertion.
- Full local suite: `401 passed` before the test correction; final rerun pending.

Verification for Stage 83:

- Route registry tests: `3 passed` after correcting decorator matching.
- Full suite final rerun pending.

### 2026-08-20 — Stage 84

- Added shared browser Logout control.
- Logout uses `POST /auth/logout` and sends the CSRF token from the readable CSRF cookie; no GET logout was added.
- Added browser component regression coverage.
- Remaining: continue browser-flow hardening and read-only parity.

### 2026-08-20 — Stage 85

- Fixed shared Logout wiring: the navigation button now invokes `AlterWeb.logout()` directly.
- Logout remains POST + CSRF protected and redirects to `/auth/login` after success.
- Added regression coverage for the actual button handler.
- Remaining: continue browser-flow hardening and read-only parity.

### 2026-08-20 — Stage 81

- Added shared `AlterWeb.table(columns, rows, empty)` component.
- Added common responsive table and empty-state styles.
- Added component regression coverage.
- No business logic, routes, auth, or production configuration changed.
- Remaining: continue the next 3–4 migration blocks.

### 2026-08-20 — Stage 80

- Added shared breadcrumb component and styling for nested Staff/Client pages.
- Added component regression coverage.
- No route behavior, auth behavior, or data access changed.
- Remaining: continue the next 3–4 migration blocks.

### 2026-08-20 — Stage 79

- Added Staff Student hub and Client hub linking migrated read-only sections.
- Added hub wiring test; no new data access or mutation introduced.
- Remaining: continue the next 3–4 migration blocks.

### 2026-08-20 — Stage 78

- Batch-expanded Staff navigation with all settings pages: Legal, Camera, Features, Limits, Branding, and Integrations.
- Added navigation coverage for the complete current Staff settings surface.
- Settings remain read-only; no secrets or settings mutations are exposed.
- Remaining: continue the next 3–4 migration blocks.

1. Review bind-phone behavior and audit events in local/staging-like tests.
2. Add a read-only route contract test proving no client payload can override `user_id` or `club_id`.
3. Choose the next non-money mutation only after its permission, CSRF, idempotency, and rollback behavior are specified.
4. Keep payment, cash, inventory, subscription purchase, freeze purchase, and schedule edits disabled in Web until those contracts are tested.

### 2026-08-20 — Stage 30

- Added regression coverage proving Web bind-phone ignores spoofed `user_id` and `club_id` payload fields.
- The mutation derives both values exclusively from `AuthContext`.
- Remaining: review the next non-money mutation contract; no new mutation is enabled by this stage.

### 2026-08-20 — Stage 31

- Added `WEB_MUTATION_CONTRACTS.md` with the reviewed next mutation contract.
- Selected Schedule edit as the next non-money mutation.
- Defined required AuthContext, `schedule_edit`, CSRF, club locking, validation, idempotency, audit, and test requirements.
- Explicitly deferred cash, sales, subscription, freeze, pricing, and audit-delete mutations.
- Remaining: implement Schedule edit only after its contract tests are added first.

### 2026-08-20 — Stage 32

- Implemented `PATCH /api/v1/staff/schedule`.
- Added `schedule_edit`, CSRF, authenticated club locking, day/discipline validation, Redis idempotency, and audit telemetry.
- Added tests for successful update, replay, invalid day, permission, and CSRF path.
- Remaining: run the full suite and review schedule payload validation before enabling any other mutation.

### 2026-08-20 — Stage 33

- Strengthened Schedule mutation validation: every lesson must be an object with `HH:MM` time and duration from 15 to 240 minutes.
- Added malformed lesson regression coverage.
- Remaining: run the full suite; keep other money/pricing mutations disabled until separately contracted.

### 2026-08-20 — Stage 34

- Added Schedule lesson field allowlist and bounds for coach/group/discipline strings and capacity.
- Added regression coverage for unapproved payload fields.
- Updated `WEB_MUTATION_CONTRACTS.md` with the allowlist.
- Remaining: close Schedule mutation review, then select and contract the next non-money operation.

### 2026-08-20 — Stage 35

- Closed the Schedule mutation review after field allowlisting and bounds tests.
- Added the next candidate contract to `WEB_MUTATION_CONTRACTS.md`: client-owned student creation.
- Defined CSRF, identity/club ownership, strict fields, duplicate detection, idempotency, audit, and rollback requirements.
- No student-creation mutation is exposed yet.
- Remaining: implement contract-first tests, then add the mutation without changing Telegram student creation.

### 2026-08-20 — Stage 36

- Added read-only Client `/api/v1/client/me` contract.
- Added Client student creation mutation: `POST /api/v1/client/students`.
- Added CSRF, authenticated user/club ownership, strict name/discipline validation, duplicate detection, Redis idempotency, and audit event.
- Added tests for identity spoofing resistance, retry idempotency, duplicate rejection, and CSRF.
- Full UI form is intentionally not exposed yet; it follows after the API contract review.
- Remaining: run full suite, then add the guarded Client student-creation page and update the matrix with the new route.

Verification for Stage 36:

- Targeted student-create tests: `3 passed`.
- Full local suite: `331 passed`.
- Test isolation was corrected so shared fake Redis state cannot hide duplicate behavior.
- Remaining: add the guarded Client student-creation page, then continue the next 3–4 migration blocks.

### 2026-08-20 — Stage 37

- Added guarded browser page `GET /client/students/new`.
- Page uses the shared shell/components, reads the CSRF cookie, sends `X-CSRF-Token`, and creates a fresh idempotency key per submission.
- Added page tests for auth, API wiring, and CSRF integration.
- Remaining: add a success redirect/list refresh after creation and continue the next client blocks.

### 2026-08-20 — Stage 38

- Added read-only Client profile API/page:
  - `GET /api/v1/client/me`
  - `GET /client/me`
- Profile exposes only AuthContext identity/source fields.
- Added profile endpoint/page tests.
- Remaining: add the post-create cabinet link/list refresh, then continue the next 3–4 blocks.

### 2026-08-20 — Stage 39

- Added post-create navigation from `/client/students/new` to `/client/cabinet`.
- Added page regression coverage proving the cabinet refresh link is present after creation.
- Remaining: continue the next 3–4 migration blocks; no additional mutation enabled in this stage.

### 2026-08-20 — Stage 40

- Added read-only settings APIs for legal, camera metadata, and feature flags.
- Responses use allowlists and exclude camera URLs/secrets.
- Added permission and club-scope tests.
- Remaining: add pages for these settings where useful, then continue the next 3–4 blocks.

### 2026-08-20 — Stage 42

- Expanded shared Web navigation with links to Overview, Forecast, Revenue, and Students.
- Added shared `replaceWithError` helper and responsive navigation styling.
- Added tests for shared navigation and error helper.
- Remaining: continue remaining page contracts and avoid page-local navigation duplication.

### 2026-08-20 — Stage 43

- Added read-only Staff Check-in API/page:
  - `GET /api/v1/staff/checkin/data`
  - `GET /staff/checkin`
- Requires `qr_checkin`, scopes all visits to the authenticated club, and exposes no check-in mutation.
- Added permission and club-scope tests.
- Remaining: continue the next 3–4 legacy blocks; keep real check-in mutation disabled until separately contracted.

### 2026-08-20 — Stage 44

- Added Client Legal API/page:
  - `GET /api/v1/client/legal/data`
  - `GET /client/legal`
- Legal response is scoped to the authenticated club and uses a client-safe allowlist without tax identifiers.
- Added club-scope, redaction, and page wiring tests.
- Remaining: continue the next 3–4 blocks; keep legal mutations disabled.

### 2026-08-20 — Stage 45

- Updated shared navigation to detect Client vs Staff context.
- Client pages now receive Cabinet/History/Subscriptions links; staff pages receive Overview/Forecast/Revenue/Students links.
- Brand link also follows the current context.
- Added navigation-context test.
- Remaining: continue the next migration blocks while keeping navigation centralized.

### 2026-08-20 — Stage 46

- Added Staff Freeze read-only API/page:
  - `GET /api/v1/staff/freeze/data`
  - `GET /staff/freeze`
- Shows only frozen students in the authenticated club and exposes no freeze mutation.
- Added permission and club-scope tests.
- Remaining: continue the next 3–4 legacy blocks; freeze purchase/edit remains deferred.

### 2026-08-20 — Stage 47

- Added Staff Student detail API/page:
  - `GET /api/v1/staff/students/{student_id}`
  - `GET /staff/students/{student_id}`
- Detail query requires both student ID and authenticated club scope; foreign-club records return 404.
- Added detail and safe-not-found tests.
- Remaining: continue the next blocks; student mutations remain behind the separate client contract.

### 2026-08-20 — Stage 48

- Added Staff Student visits API/page:
  - `GET /api/v1/staff/students/{student_id}/visits`
  - `GET /staff/students/{student_id}/visits`
- Visits are double-scoped by authenticated club and student ID after student ownership is verified.
- Added tests for double scope and permission denial.
- Remaining: continue the next blocks; visit/check-in mutations remain disabled.

### 2026-08-20 — Stage 49

- Added Staff Student payment history API/page:
  - `GET /api/v1/staff/students/{student_id}/payments`
  - `GET /staff/students/{student_id}/payments`
- Payments require `analytics_view`, are restricted to the authenticated club/student, and include confirmed records only.
- No payment mutation or provider secret is exposed.
- Added payment scope and permission tests.
- Remaining: continue the next detail blocks; money mutations stay deferred.

### 2026-08-20 — Stage 50

- Added Staff Student Discounts API/page:
  - `GET /api/v1/staff/students/{student_id}/discounts`
  - `GET /staff/students/{student_id}/discounts`
- Discounts are joined through `DiscountAssignment`, filtered by student and club, and limited to active records.
- Added scope and permission tests; no discount mutation exposed.
- Remaining: continue the next detail blocks; pricing mutations remain deferred.

### 2026-08-20 — Stage 51

- Added Client Purchases API/page:
  - `GET /api/v1/client/purchases/data`
  - `GET /client/purchases`
- Cart orders are scoped by authenticated `user_id` and `club_id`, limited to confirmed orders, and expose no provider secrets.
- Added scope and page wiring tests.
- Remaining: continue the next client/staff read-only blocks; purchase mutations remain deferred.

### 2026-08-20 — Stage 52

- Expanded centralized Client navigation with Purchases, Freeze, Profile, and Legal links.
- All client routes now share one navigation source; no page-local client menu was added.
- Added navigation coverage for the complete client read-only set.
- Remaining: continue the next migration blocks and keep purchase/freeze mutations deferred.

### 2026-08-20 — Stage 53

- Expanded centralized Staff navigation with all currently implemented read-only sections: Cash, Sales, Audit, Schedule, Products, Discounts, Tariffs, Check-in, and Freeze.
- Added coverage for every staff navigation target.
- Remaining: continue remaining legacy contracts and keep all deferred mutations disabled.

### 2026-08-20 — Stage 54

- Added Staff profile page `GET /staff/me` backed by `/auth/me`.
- Page is limited to staff/owner AuthContext and exposes only role, club, and permission count.
- Added profile wiring and client-denial tests.
- Remaining: continue remaining legacy contracts; no new mutation enabled.

### 2026-08-20 — Stage 55

- Added Phase 0 login entry contract: `GET /auth/login`.
- It exposes only the Telegram one-time exchange method; no credentials or IDs are accepted from the URL.
- Added authenticated staff entry route `GET /staff` redirecting to Overview.
- Added auth entry and staff/client access tests.
- Remaining: complete Phase 0 review and continue remaining legacy contracts.

### 2026-08-20 — Stage 56

- Added Telegram exchange test for staff role/permission resolution inside the selected club.
- Confirmed exchange response does not expose one-time `init_data`.
- Phase 0 auth regression coverage now includes owner exchange, staff permissions, invalid auth, session round-trip, logout, CSRF, and cross-club scoping.
- Remaining: finish Phase 0 checklist review, then continue deferred legacy mutations one contract at a time.

### 2026-08-20 — Stage 57

- Added `PHASE0_CHECKLIST.md` with completed auth foundation, deferred items, verification, and next-phase boundary.
- Phase 0 local verification is complete: `363 passed`.
- Production deployment remains explicitly deferred; branch remains `web-migration/phase-0-auth`.
- Next: continue read-only parity or implement the next reviewed mutation contract with feature-flag/rollback notes.

### 2026-08-20 — Stage 58

- Added production-safe feature flag `WEB_SCHEDULE_MUTATIONS_ENABLED` for Schedule mutation.
- Default behavior is disabled (`404 feature_disabled`); tests explicitly enable it when exercising the contract.
- Updated mutation contract with the flag requirement and added default-disabled regression coverage.
- Remaining: add equivalent feature-flag/rollback notes before enabling any future mutation.

### 2026-08-20 — Stage 59

- Added production-safe feature flag `WEB_CLIENT_STUDENT_MUTATIONS_ENABLED`.
- Client student creation now defaults to disabled and returns `404 feature_disabled` until explicitly enabled.
- Updated mutation contract and added default-disabled regression coverage.
- Remaining: add equivalent rollout notes for bind-phone and keep all money mutations disabled.

Verification for Stage 58:

- Schedule mutation tests after import correction: `7 passed`.
- Full local suite: `364 passed`.

Verification for Stage 50:

- Targeted Discounts detail tests: `2 passed` after correcting the missing model import.
- Full local suite: pending final rerun.

### 2026-08-20 — Stage 41

- Added browser pages:
  - `/staff/settings/legal`
  - `/staff/settings/camera`
  - `/staff/settings/features`
- Pages use one shared settings helper and the common Web shell.

### 2026-08-20 — Stage 60

- Создан изолированный staging SpeedyCRM в `/root/speedycrm-staging`, Compose-проект `speedycrm-staging`, отдельная сеть и volumes.
- API слушает только `127.0.0.1:18000`; публичный nginx/domain не добавлялся.
- База восстановлена из live `gym_db` в отдельный volume; в staging обнулены 2 `clubs.bot_token`, live database не изменялась.
- `/health` и `/ready` возвращают 200, DB/Redis ok, `bots_active: 0`, staging API healthy.
- Live-контейнеры продолжили работать; `/root/alter` не использовался. Ветка `web-migration/phase-0-auth`, production deploy не выполнялся.
- Tunnel: `ssh -N -L 18000:127.0.0.1:18000 -i C:\Users\79615\.ssh\alter_agent root@77.73.131.175`
- Smoke через tunnel: `/health` 200, `/ready` 200, `/auth/login` 200; `/auth/me` и `/staff` без сессии корректно возвращают 401. Сторонние `/api/*` пути не являются текущим web-контрактом и не использовались как критерий.
- Осталось: browser smoke через tunnel, auth/read-only проверка и отдельное решение по mutations; production rollout запрещён.
- Найден и исправлен дефект web route: `/staff/audit` был продублирован на student hub handler и до auth отдавал 422 вместо 401. Дублирующий decorator удалён, добавлен regression test; targeted suite: `4 passed`.
- Следующий шаг: собрать этот фикс в staging и повторить smoke `/staff/audit`, затем продолжить авторизованный browser smoke.
- Фикс собран и перезапущен только в staging; повторная проверка `/staff/audit` через tunnel: 401 без сессии, `/ready`: 200, staging API healthy.
- Полный локальный regression suite после фикса: `411 passed`.
- Повторная серверная проверка: staging API/DB/Redis healthy; live `gym_*` и `githubio-test-redis-1` healthy; staging слушает только `127.0.0.1:18000`, публичные 80/443 принадлежат live nginx.
- Осталось: получить тестовую Telegram Web авторизацию для staging и пройти авторизованные staff/client страницы; без неё проверяется только корректное auth-gating. Затем — review перед любым rollout.
- Добавлен воспроизводимый `scripts/staging_smoke.ps1`: через SSH tunnel проверяет health/readiness, auth entry и 32 защищённых staff/client web routes. Smoke пройден: все ожидаемые статусы корректны.
- Проверен негативный auth flow staging: exchange с корректной JSON-формой, но invalid Telegram `init_data`, возвращает 401; последующий `/auth/me` остаётся 401. Сессия не создаётся.
- Авторизованный smoke пока заблокирован отсутствием отдельного staging Telegram bot token/test account; намеренно не подделываем Redis session и не используем live bot credentials.
- Добавлен `STAGING_RUNBOOK.md` с tunnel, smoke, lifecycle и DB-refresh процедурами. Runbook не содержит секретов и явно запрещает операции с live/ALTER.
- Production-readiness обновлён: suite `411 passed`, staging gate отмечен выполненным для изолированного окружения и unauthenticated smoke. Production gate остаётся закрытым до отдельного Telegram test account, browser smoke, backup/rollback review и явного approval.
- При настройке staging bot PostgreSQL diagnostic раскрыл token из-за попытки записать один token сразу в две уникальные club-записи. Token немедленно удалён с сервера; staging DB подтверждён с `0` bot tokens. Требуется отозвать этот token через BotFather и создать новый. Helper исправлен: новый token будет назначаться только первой staging club-записи.
- Новый staging bot token принят без вывода в логи, записан только в одну staging club-запись (`1` non-null token), API перезапущен только в staging. `/health` и `/ready` — 200, `bots_active: 1`, DB/Redis ok. Live и ALTER не затронуты.
- Следующий production-readiness блок: получить реальный `init_data` от staging bot/test account. Для полноценного Telegram WebApp browser smoke потребуется безопасный HTTPS staging entry или ручная передача одноразового init_data без публикации token.
- Добавлен локальный `scripts/staging_exchange.ps1`: принимает одноразовый `init_data` только через локальный prompt, вызывает staging через tunnel и не сохраняет значение. Club ID staging: `1`.
- Для SSH-only staging выставлен `COOKIE_SECURE=0` только в `docker-compose.staging.yml`: tunnel локально использует HTTP, а production cookie policy не изменялась. Staging API перезапущен, `/ready` 200, `bots_active: 1`.
- По согласованному варианту 1 подготовлен отдельный HTTPS staging entry `https://staging.speedycrm.ru:18443`: DNS уже указывает на сервер, wildcard-сертификат действителен, отдельный nginx не использует live 80/443 и подключён к staging network. Это временно публичная точка только для Telegram WebApp smoke.
- Добавлен `/auth/web-entry?club_id=1`: отдельная Telegram WebApp entry-страница получает `Telegram.WebApp.initData`, отправляет его на штатный exchange и не отображает/сохраняет init data. Добавлен contract test.
- Web entry собран и развернут только в staging; `https://staging.speedycrm.ru:18443/ready` — 200, entry содержит Telegram WebApp SDK. Staging API/DB/Redis healthy, `bots_active: 1`.
- Для запуска из Telegram в отдельном staging-боте нужно установить Menu Button/Web App URL: `https://staging.speedycrm.ru:18443/auth/web-entry?club_id=1`. Live-бота и его кнопки не менять.
- Получен первый реальный browser smoke внутри staging Telegram WebApp: открыт `SpeedyCRM / STAFF WEB`, авторизация через staging bot прошла, Forecast и общая navigation отрисовались. Полный сценарий cookies/refresh/logout, всех страниц и cross-club isolation ещё не закрыт.
- Пользователь подтвердил полный staff browser smoke в staging: все кнопки/разделы работают, WebApp закрывается и открывается повторно корректно. Logout/revocation и client/cross-club сценарии остаются следующими проверками.
- Реальный staging logout smoke подтверждён скриншотами: после Logout открывается `/auth/login` без staff-контента, повторное открытие через меню staging-бота выполняет новый exchange и возвращает в Staff Web. Staff session/revocation gate закрыт.
- Следующий блок: client WebApp с отдельным Telegram test account, привязанным к staging club; затем cross-club denial. Текущий staging account — staff/owner, поэтому client cabinet им проверять нельзя.
- Найден auth gap client Web: Telegram exchange разрешал только owner/staff. Добавлен scoped client fallback по `User.club_id` или `Student.club_id + parent_id` после staff-проверки; чужой club не проходит. Добавлен contract test; staging deploy и client browser smoke — следующий шаг.
- Client auth fix deployed only to staging: targeted tests `8 passed`, `/ready` 200, `bots_active: 1`. В staging club 1 сейчас `0` student parents; данные client-контуров есть в club 2 (`76` parents), поэтому нужен отдельный staging client test account/Telegram ID и выбранный club для browser smoke.
- Client Telegram smoke признан необязательным для цели миграции. Native auth переведён с SMS на Email OTP: обновлён контракт с Redis/session/CSRF правилами и feature-flag rollout. Telegram остаётся fallback; native auth пока не включён.
- Добавлено nullable `users.email` и обратимая Alembic-миграция `z8a9b0c1d2e3` для будущего Email OTP; добавлен migration contract test. Existing users не требуют немедленного заполнения email.
- Email schema проверена: полный suite `414 passed`; staging обновлён миграцией, `users.email` доступно, `/ready` 200, `bots_active: 1`. Native Email OTP всё ещё выключен до mail adapter/provider.
- Реализован feature-gated Email OTP core: `/auth/native/request` и `/auth/native/verify`, Redis hash/TTL/attempt limit, SMTP adapter, server-side session/CSRF и club-scoped actor resolution. Добавлен contract test; flag `WEB_NATIVE_AUTH_ENABLED=0`, поэтому flow не активен.
- После исправления порядка деклараций моделей полный suite: `415 passed`, `git diff --check` без ошибок. Email OTP endpoints остаются выключенными флагом.
- Email OTP core собран в staging: `/auth/native/request` возвращает 404 при default-disabled flag, `/auth/login` 200, `/ready` 200, `bots_active: 1`. Telegram flow не изменён.
- Добавлен общий role-agnostic email binding для client/staff/owner: `/auth/native/email/request` и `/auth/native/email/verify`, только из AuthContext с CSRF, club scope и отдельным `WEB_NATIVE_EMAIL_BINDING_ENABLED=0`. Добавлен contract test; пока не включён.
- После исправления порядка web_context полный suite: `416 passed`. Email binding остаётся disabled по умолчанию; роли client/staff/owner используют общий backend/AuthContext.
- Role-agnostic email binding собран в staging с default-disabled flags; staging `/ready` 200, Telegram `/auth/login` 200, `bots_active: 1`. Native endpoint не включался.
- SMTP adapter сверён read-only с ALTER: перенесены только совместимые имена/поведение (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_USE_TLS`), credentials ALTER не копировались. Добавлен adapter contract test; native flags остаются disabled.
- SMTP reference block verified: targeted `3 passed`, full suite `417 passed`. Настройки SMTP в staging пока не добавлялись и native Email OTP не включался.
- Подготовлен отдельный optional `/root/speedycrm-staging/.staging-mail.env`, подключаемый только staging Compose; шаблон `.staging-mail.env.example` и runbook добавлены. Реальные SMTP secrets не копируются из ALTER и не попадают в git; native flags остаются `0`.
- Staging SMTP secret file найден, permissions `600`, имена всех 6 SMTP-переменных присутствуют без раскрытия значений; staging API перезапущен, `/ready` 200, `bots_active: 1`. Email flags ещё не включены.
- Подготовлен helper `scripts/enable_staging_email_test.sh` для staging-only email теста пользователя `1271717628`; он меняет только staging DB, включает только staging flags и перезапускает только staging API. Выполнение ожидает завершения проверки команды без shell-quoting ошибок.
- SMTP test enabled only in staging after explicit approval; compose now loads `.staging-mail.env`, env names present without values exposed, and `/auth/native/request` for `omarovadam405@gmail.com` returned 200. Verify code must be entered locally via `scripts/staging_native_verify.ps1`.
- Диагностика показала, что первый helper не передал heredoc в `docker exec` без `-i`: email фактически не записался, поэтому generic 200 был без отправки. Исправлено добавлением `docker exec -i`; письмо повторяется после подтверждения записи в staging DB.
- После исправления записи email SMTP request дошёл до delivery adapter, но вернул 500 `SMTP is not configured`; добавлен безопасный `scripts/check_staging_smtp.sh`, который показывает только set/empty переменных внутри staging API.
- Следующая диагностика показала реальную причину: staging SMTP host — `smtp.gmail.com:587`, контейнер получает `Network is unreachable`. Credentials загружены; требуется сетевой маршрут/разрешение outbound SMTP, не изменение auth-кода.
- После сетевого теста (`host timeout`, `container OSError`) native flags удалены из staging secret file и API перезапущен, `/ready` 200. SMTP delivery failure теперь будет безопасным `503 email_delivery_unavailable`, без внутренних traceback.
- Проверен firewall: UFW inactive, `iptables OUTPUT ACCEPT`, host default route присутствует. Дополнительное allow-правило для 587 не требуется; timeout/Network unreachable к Gmail находится вне локального firewall. Native flags остаются выключены.
- Подготовлен Yandex Cloud Postbox HTTPS adapter через boto3/443 как альтернатива заблокированному SMTP 587. Secrets используются только из staging env и не логируются; добавлен contract test. Требуется staging rebuild и безопасный send test.
- Yandex adapter проверен локально, full suite: `418 passed`; boto3 добавлен в requirements. Staging rebuild/send test — следующий шаг, native flags пока выключены после SMTP egress failure.
- Yandex Postbox staging send test достиг adapter, но вернул безопасный `503`; добавлено server-side exception logging без secrets для диагностики provider rejection. Native flags временно включены только staging.
- Postbox HTTPS transport работает, но API ответил `ResponseParserError` с request/trace IDs без XML; flags удалены из staging и API healthy. Следом нужно сверить Yandex IAM role/API operation (`ses` vs `sesv2`) и sender identity, затем повторить один тест.
- Добавлен non-sending `scripts/check_postbox_api.py` для проверки `GetSendQuota` через `ses`/`sesv2` без раскрытия keys и без отправки письма. Следующий staging diagnostic.
- Postbox diagnostic: `ses` endpoint отвечает `ResponseParserError`, `sesv2` не предоставляет `GetSendQuota` в этом API. Письма не отправлялись; native flags остаются выключены. Нужно сверить именно SendEmail API/адрес отправителя по Postbox docs, а не делать повторные blind sends.
- Подготовлен одноразовый `scripts/send_postbox_probe.py` для реального `sesv2.SendEmail` на staging test email; secrets не печатаются, приложение/native flags не включаются.
- Реальный Postbox probe успешно отправил письмо (`sesv2.SendEmail: send=ok`). Adapter переключён с несовместимого `ses` на `sesv2`; следующий шаг — staging rebuild и один Email OTP request.
- Adapter переключён на `sesv2`, staging rebuilt; с native flags только в staging `/auth/native/request` вернул 200 для `omarovadam405@gmail.com`. Письмо отправлено через Yandex Postbox HTTPS; следующий шаг — локально ввести код и проверить `/auth/me` с `auth_source=email`.
- Email OTP официально подтверждён в staging: `verify=200`, `/auth/me` показал `auth_source=email`, `actor_type=owner`, `user_id=1271717628`, `club_id=2`. Общий backend/AuthContext работает для native email login.
- Следующее: добавить UI входа/привязки email в общий Web shell, протестировать staff/client роли через email и после этого обновить production gates. Production flags остаются выключенными.
- Добавлен общий `/auth/email-profile` и navigation link для всех ролей; UI выполняет CSRF-защищённую request/verify привязку email через общий backend. Добавлен page contract test.
- UI email profile проверен локально; full suite `419 passed`. Staging flags остаются включёнными только для дальнейшего Email owner smoke, production flags не менялись.
- Первый UI smoke email profile подтверждён скриншотом: account/email page открывается, verified email отображается. Убран дублирующийся текст формы для уже подтверждённого email; теперь verified account показывает только статус и passwordless availability.
- Добавлена функциональная public page `/auth/native-login?club_id=2` для email request/verify без Telegram. Визуальная полировка намеренно отложена; добавлен contract test.
- Native login page contract passed; full suite: `420 passed`, `git diff --check` clean. Следом staging rebuild и logout → `/auth/native-login?club_id=2` smoke.
- Исправлен browser validation bug: f-string превращал `[0-9]{6}` в `[0-9]6`; pattern экранирован, targeted test `1 passed`, staging rebuilt, `/ready` 200.
- Реальный email login smoke подтверждён: после OTP открыт `/staff/overview` через native email session; screenshot показал staging metrics. После smoke staging очищен: ручной email удалён из staging user, native flags отключены, временный public HTTPS nginx остановлен (`PUBLIC 000`), SSH-only API `/ready` 200. Live/ALTER не затронуты.
- Начат security-пакет: Email binding request ограничен по email и IP/club (`3` запроса за `300` секунд), добавлен security contract test. Все изменения только в `web-migration/phase-0-auth`.
- В общий navigation добавлен переключатель `EN/RU` с сохранением выбора в localStorage и `document.lang`; полноценный перевод текстов и финальный дизайн оставлены отдельным последним этапом.
- Security/language пакет проверен: full suite `422 passed`, `git diff --check` clean. Staging remains isolated and production branch untouched.
- Current branch deployed to SSH-only staging: `/ready` 200, public staging nginx exited, API/DB/Redis healthy. EN/RU switcher and OTP rate-limit code are present; native flags remain disabled.
- Added executable OTP security tests for single-use/attempt bounds, TTL and email+IP/club rate limits; updated release readiness. Next: complete role/cross-club matrix and data anonymization review before any production approval.
- Added native role/cross-club contract matrix for owner, staff and client actors; selected club scope is required for both Telegram fallback and Email native auth. Browser cross-club scenario remains pending a non-owner client fixture.
- Role/scope package verified: targeted `4 passed`, full suite `426 passed`. No production deploy or master merge.
- Seeded staging-only synthetic client fixture: `user_id=990000001`, club 1, one student, same test email; added seed/cleanup helpers. Native flags enabled only in staging for client Web smoke; fixture must be removed after verification.
- Restricted pages enforce `analytics_view`/`qr_checkin`; no settings mutation was exposed.

## 2026-08-20 — final diff and rollback review

- Просмотрен `git diff master...HEAD`: `117` файлов только в web/auth/staging/docs/tests/migration support контуре.
- Проверен `.github/workflows/deploy.yml`: production deploy запускается только для `master`/`main` или ручного workflow; текущая migration branch не запускает deploy.
- Зафиксированы commits: base `master`=`57ef297fa156cd28ca59b3c8600c8b167ecad142`, staging HEAD=`eafffef12189665c0bbda32274704fe7ff82aa89`.
- Добавлен `ROLLBACK_RUNBOOK.md` с guarded rollback procedure; команды rollback не выполнялись.
- Добавлены workflow gate tests; production backup/deploy остаются только после явного approval.

### Следующий пакет

- Прогнать полный suite после workflow/runbook tests.
- Финальный human decision: оставить ветку для дальнейшего UI polish или запросить merge approval.

## 2026-08-20 — final staging auth smoke

- Запущен воспроизводимый `scripts/staging_smoke.ps1` через SSH tunnel.
- Проверены `/health=200`, `/ready=200`, `/auth/login=200`, `/auth/me=401` без сессии.
- Все 32 защищённых staff/client Web routes корректно вернули `401` без авторизации.
- Это закрывает automated auth-gating smoke; authenticated Telegram staff smoke был подтверждён ранее вручную в отдельном staging bot.
- Production Telegram path не менялся; live, ALTER и `master` не затронуты.

### Следующий пакет

- Провести финальную human review изменённых файлов и подтвердить rollback image/commit.
- Оставить UI spacing/полные переводы на отдельный финальный дизайн-этап.
- После explicit approval решить merge в `master`; до этого production flags и deploy остаются закрыты.

## 2026-08-20 — migration compatibility gate

- Свежий staging dump восстановлен в отдельную `crm_migration_check_20260820`.
- На этой test DB выполнены Alembic `downgrade z8a9b0c1d2e3 -> y7z8a9b0c1d2` и повторный `upgrade head`; обе операции успешны.
- Временная migration-check DB удалена после проверки; staging `/ready=200`.
- Обновлён production checklist: migration upgrade/downgrade gate отмечен выполненным.
- Live, ALTER и `master` не затрагивались; production deploy не выполнялся.

### Следующий пакет

- Завершить техническую часть: финальный локальный suite и `git diff --check` после документирования.
- Оставшиеся пункты: Telegram regression smoke, финальный UI/translation pass и явное решение о merge/production rollout.

## 2026-08-20 — staging backup and restore gate

- В staging создан custom-format PostgreSQL backup из отдельной staging DB, размер артефакта `174K`.
- Проверка `pg_restore --list` прошла успешно.
- Выполнен restore-check в отдельную staging test DB `crm_restore_check_20260820`; restore прошёл, затем test DB удалена.
- После операций staging `/ready=200`; live `/root/github.io-test` и ALTER `/root/alter` не использовались.
- Production backup пока не выполнялся: он требует отдельного явного approval и не нужен для безопасной проверки staging.
- В серверной копии backup helper обнаружен CRLF/permission issue; staging-проверка выполнена напрямую теми же pg_dump/pg_restore операциями, код репозитория не менялся.

### Следующий пакет

- Выполнить staging migration refresh/upgrade compatibility check на копии staging DB.
- Проверить downgrade email migration только на временной test DB.
- После этого остаются UI/translation polish и отдельное решение о production rollout.

## 2026-08-20 — final review package started

- Выполнен read-only diff review `master...HEAD`: изменения находятся в web/auth/staging/docs/tests контуре; production workflow не запускался.
- Secret hygiene review не обнаружил staging/live credentials в новых web-asset и contract-файлах; реальные secrets остаются только во внешних server env files.
- Проверена последняя Email migration: revision `z8a9b0c1d2e3` → `y7z8a9b0c1d2`, nullable upgrade и downgrade присутствуют.
- Добавлен [PRODUCTION_MIGRATION_CHECKLIST.md](PRODUCTION_MIGRATION_CHECKLIST.md) с completed/required gates и safety rules.
- `master`, live SpeedyCRM и ALTER не затронуты; merge/push не выполнялись.

### Следующий пакет

- Выполнить backup artifact verification и restore-check только в отдельной test DB.
- Сверить staging migration upgrade/downgrade после refresh из актуального snapshot.
- После закрытия технических gates отдельно решить вопрос merge в `master`; автоматический production deploy без явного approval запрещён.

## 2026-08-20 — production-readiness package: isolation, backup and rollback

- Добавлены cross-club contract checks для native client auth и read-only client routes: actor scope проверяется до выдачи web session/данных.
- Добавлены safety checks staging fixture helpers и проверка, что они не могут ссылаться на live/ALTER.
- Добавлены contract checks backup/restore: custom PostgreSQL dump, `pg_restore --list`, lock/retention, restore только в отдельную test DB.
- Добавлена проверка обратимой nullable email migration с корректным parent revision.
- `RELEASE_READINESS.md` обновлён: зафиксированы текущие `433` → `437` tests и backup/rollback procedure; production gates остаются закрыты.
- Targeted suite: `10 passed`; полный suite: `437 passed`; `git diff --check` чист.
- Все изменения только в `web-migration/phase-0-auth`; live SpeedyCRM, ALTER и `master` не затронуты.

### Следующий пакет

- Выполнить human diff review и проверить фактические backup/restore prerequisites на отдельной test DB, без live restore.
- Подготовить финальный migration compatibility checklist и список rollback commands.
- После этого — финальный UI/translation polish и отдельное решение о production rollout.

## 2026-08-20 — controlled staging client fixture cycle

- На сервере выполнен только staging-only цикл `seed_staging_client.sh` → fixture создан (`user/student INSERT 1`) → `cleanup_staging_client.sh` → обе записи удалены (`DELETE 1`).
- После controlled cycle staging API был перезапущен helper-скриптами; первый запрос попал в короткое окно рестарта (`000`), повторная readiness-проверка успешна: `/ready=200`.
- Live `/root/github.io-test`, ALTER `/root/alter` и production branch не использовались.
- Полноценный client browser smoke с отдельным Telegram client account пока не выполнялся; native client smoke возможен через Email OTP при временном staging flag.

### Следующий пакет

- Провести cross-club denial на staging через изолированный тестовый actor/fixture либо локальный route-level сценарий.
- Подготовить backup/rollback/migration compatibility review и закрыть production-readiness checklist.

## 2026-08-20 — staging fixture and client isolation package

- Добавлены contract tests для staging seed/cleanup helpers: они используют только `speedycrm_staging_db` и `/root/speedycrm-staging`, не содержат ссылок на live/ALTER и работают с зарезервированным synthetic ID `990000001`.
- Добавлены проверки client read-only routes: actor context обязателен, выборка привязана к `actor.club_id`, ответы помечены `read_only`.
- Targeted suite: `10 passed`; полный suite: `433 passed`; `git diff --check` чист.
- Изменения только в `web-migration/phase-0-auth`; `master`, live SpeedyCRM и ALTER не затронуты.

### Следующий пакет

- На staging выполнить controlled client fixture smoke при необходимости: seed → native login → client pages → cleanup.
- Проверить cross-club denial отдельным тестовым actor/club без использования live credentials.
- После этого перейти к backup/rollback/migration compatibility review.

## 2026-08-20 — security regression package

- Добавлены contract tests для logout: server-side session revocation и очистка session/CSRF cookies.
- Добавлена проверка, что CSRF принимается только для существующей серверной сессии.
- Добавлен OTP edge-case test: неизвестный и удалённый/истёкший OTP не может быть использован.
- Targeted suite: `8 passed`; полный suite: `430 passed`; `git diff --check` чист.
- Изменения только в ветке `web-migration/phase-0-auth`; `master`, live SpeedyCRM и ALTER не затронуты.

### Следующий пакет

- Подготовить staging-only client/staff email fixtures для browser smoke и проверить cross-club denial.
- После smoke удалить fixtures, отключить staging flags и записать результат.
- Затем перейти к backup/rollback/migration compatibility review перед production approval.

## 2026-08-20 — контрольная точка перед следующим пакетом

- Ветка: `web-migration/phase-0-auth`; рабочее дерево чистое, `master` не изменялся и production deploy не выполнялся.
- Выполнен полный локальный regression suite: `426 passed`.
- `git diff --check` пройден без ошибок.
- Staging остаётся изолированным и SSH-only: API доступен через tunnel на `127.0.0.1:18000`; live `/root/github.io-test` и ALTER `/root/alter` не затрагивались.
- Native Email OTP подтверждён в staging: Yandex Postbox send, verify, server-side session, `auth_source=email`, role/club scope и logout; добавлены общий email-profile UI и EN/RU переключатель.
- Временные тестовые данные удалены, production flags не включались.

### Следующие шаги

1. Закрыть browser/security сценарии для client/staff/owner: logout/revocation, invalid/expired OTP, rate limit и cross-club denial.
2. Протестировать native email binding для staff/client ролей на staging-only данных; после smoke удалить тестовые записи и выключить flags.
3. Провести review staging data и подготовить backup/rollback/migration compatibility checklist.
4. Затем — общий UI/переводы и финальный production-readiness review. Merge в `master` и production deploy только после явного подтверждения.
- Added settings page tests; remaining: continue the next 3–4 migration blocks.
