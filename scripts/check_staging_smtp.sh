#!/usr/bin/env bash
set -euo pipefail

for key in SMTP_HOST SMTP_PORT SMTP_USERNAME SMTP_PASSWORD SMTP_FROM_EMAIL SMTP_USE_TLS; do
  value="$(docker exec speedycrm_staging_api printenv "$key" 2>/dev/null || true)"
  if [ -n "$value" ]; then
    echo "$key=set"
  else
    echo "$key=empty"
  fi
  unset value
done
