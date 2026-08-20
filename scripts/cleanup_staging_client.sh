#!/usr/bin/env bash
set -euo pipefail

docker exec speedycrm_staging_db psql -U postgres -d crm_db -c 'DELETE FROM students WHERE parent_id = 990000001; DELETE FROM users WHERE user_id = 990000001;'
sed -i '/^WEB_NATIVE_AUTH_ENABLED=/d;/^WEB_NATIVE_EMAIL_BINDING_ENABLED=/d' /root/speedycrm-staging/.staging-mail.env
cd /root/speedycrm-staging
docker compose -p speedycrm-staging -f docker-compose.staging.yml up -d --force-recreate api
echo "staging client fixture removed"
