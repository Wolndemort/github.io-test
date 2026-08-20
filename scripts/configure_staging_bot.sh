#!/usr/bin/env bash
set -euo pipefail

SECRETS=/root/speedycrm-staging/.staging-secrets
DB_CONTAINER=speedycrm_staging_db

test -s "$SECRETS"
if grep -q '^STAGING_BOT_TOKEN=' "$SECRETS"; then
  token="$(sed -n 's/^STAGING_BOT_TOKEN=//p' "$SECRETS" | head -n 1)"
else
  token="$(head -n 1 "$SECRETS")"
fi
test -n "$token"
club_id="$(docker exec "$DB_CONTAINER" psql -U postgres -d crm_db -Atc 'SELECT id FROM clubs ORDER BY id LIMIT 1')"
test -n "$club_id"

docker exec "$DB_CONTAINER" psql -U postgres -d crm_db \
  -c "UPDATE clubs SET bot_token = '$token' WHERE id = $club_id"

unset token
echo "staging bot token configured for one staging club"
