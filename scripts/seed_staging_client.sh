#!/usr/bin/env bash
set -euo pipefail

docker exec -i speedycrm_staging_db psql -U postgres -d crm_db <<'SQL'
INSERT INTO users (user_id, club_id, is_accepted, full_name, email)
VALUES (990000001, 1, true, 'Staging Client Smoke', 'omarovadam405@gmail.com')
ON CONFLICT (user_id) DO UPDATE SET club_id = 1, is_accepted = true, full_name = 'Staging Client Smoke', email = 'omarovadam405@gmail.com';
DELETE FROM students WHERE parent_id = 990000001;
INSERT INTO students (club_id, parent_id, name, discipline, balance_lessons, can_freeze, is_frozen)
VALUES (1, 990000001, 'Staging Client Student', 'boxing', 10, 1, 0);
SQL

MAIL_ENV=/root/speedycrm-staging/.staging-mail.env
grep -q '^WEB_NATIVE_AUTH_ENABLED=' "$MAIL_ENV" || echo 'WEB_NATIVE_AUTH_ENABLED=1' >> "$MAIL_ENV"
grep -q '^WEB_NATIVE_EMAIL_BINDING_ENABLED=' "$MAIL_ENV" || echo 'WEB_NATIVE_EMAIL_BINDING_ENABLED=1' >> "$MAIL_ENV"
cd /root/speedycrm-staging
docker compose -p speedycrm-staging -f docker-compose.staging.yml up -d --force-recreate api
echo "staging client fixture seeded"
