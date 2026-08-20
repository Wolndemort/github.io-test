#!/usr/bin/env bash
set -euo pipefail

SECRETS=/root/speedycrm-staging/.staging-secrets
DB_CONTAINER=speedycrm_staging_db

test -s "$SECRETS"
token="$(awk -F= '$1 == "STAGING_BOT_TOKEN" {print substr($0, index($0,$2)); exit}' "$SECRETS")"
test -n "$token"
club_id="$(docker exec "$DB_CONTAINER" psql -U postgres -d crm_db -Atc 'SELECT id FROM clubs ORDER BY id LIMIT 1')"
test -n "$club_id"

docker exec "$DB_CONTAINER" psql -U postgres -d crm_db \
  -c "UPDATE clubs SET bot_token = '$token' WHERE id = $club_id"

unset token
echo "staging bot token configured for one staging club"
