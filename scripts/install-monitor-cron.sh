#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${MONITOR_PROJECT_DIR:-/root/github.io-test}"
ENV_FILE="${MONITOR_ENV_FILE:-$PROJECT_DIR/.monitor.env}"
CRON_LINE="*/2 * * * * cd $PROJECT_DIR && set -a && . $ENV_FILE && set +a && /bin/bash $PROJECT_DIR/scripts/monitor-health.sh >> /var/log/aaaa-monitor.log 2>&1"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE with MONITOR_BOT_TOKEN and MONITOR_CHAT_ID" >&2
  exit 1
fi

(crontab -l 2>/dev/null | grep -v 'scripts/monitor-health.sh' || true; echo "$CRON_LINE") | crontab -
echo "Installed monitor cron: every 2 minutes"
