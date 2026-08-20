# SpeedyCRM staging runbook

Staging расположен на том же сервере, но полностью отделён от live:

- directory: `/root/speedycrm-staging`
- compose project: `speedycrm-staging`
- API bind: `127.0.0.1:18000`
- database/Redis/network/volumes: staging-only
- production directories `/root/github.io-test` and `/root/alter` must not be used by these commands

## Access through SSH tunnel

```powershell
ssh -N -L 18000:127.0.0.1:18000 -i C:\Users\79615\.ssh\alter_agent root@77.73.131.175
```

In another terminal:

```powershell
.\scripts\staging_smoke.ps1
```

## Server checks

```bash
cd /root/speedycrm-staging
docker compose -p speedycrm-staging -f docker-compose.staging.yml ps
curl -fsS http://127.0.0.1:18000/health
curl -fsS http://127.0.0.1:18000/ready
```

## Start/rebuild staging

```bash
cd /root/speedycrm-staging
docker compose -p speedycrm-staging -f docker-compose.staging.yml up -d --build
```

## Stop/remove staging only

```bash
cd /root/speedycrm-staging
docker compose -p speedycrm-staging -f docker-compose.staging.yml down
```

Do not add `--volumes` unless the staging database is intentionally being destroyed.

## Database refresh rule

A refresh is performed only into `speedycrm_staging_db` and its staging volume. After restoring a live dump into staging, null Telegram bot tokens in staging:

```bash
docker exec gym_db pg_dump -U postgres -d crm_db --no-owner --no-privileges \
  | docker exec -i speedycrm_staging_db psql -U postgres -d crm_db
docker exec speedycrm_staging_db psql -U postgres -d crm_db \
  -c 'UPDATE clubs SET bot_token = NULL'
```

Never restore into `gym_db`; never copy staging data back to live.

## Current limitation

Authorized browser smoke requires a separate staging Telegram bot token and test account. Live bot credentials must not be reused. Until then, use the unauthenticated smoke and local test suite.

When the separate token is present in `/root/speedycrm-staging/.staging-secrets`, configure only the staging database with the server-side helper:

```bash
bash /root/speedycrm-staging/scripts/configure_staging_bot.sh
```
