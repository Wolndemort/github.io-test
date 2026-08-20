#!/usr/bin/env bash
set -euo pipefail

DB_CONTAINER=speedycrm_staging_db
MAIL_ENV=/root/speedycrm-staging/.staging-mail.env

test -s "$MAIL_ENV"
docker exec -i "$DB_CONTAINER" psql -U postgres -d crm_db <<'SQL'
UPDATE users SET email = 'omarovadam405@gmail.com' WHERE user_id = 1271717628;
SQL

grep -q '^WEB_NATIVE_AUTH_ENABLED=' "$MAIL_ENV" || echo 'WEB_NATIVE_AUTH_ENABLED=1' >> "$MAIL_ENV"
grep -q '^WEB_NATIVE_EMAIL_BINDING_ENABLED=' "$MAIL_ENV" || echo 'WEB_NATIVE_EMAIL_BINDING_ENABLED=1' >> "$MAIL_ENV"

cd /root/speedycrm-staging
docker compose -p speedycrm-staging -f docker-compose.staging.yml up -d api
echo "staging email test enabled"
