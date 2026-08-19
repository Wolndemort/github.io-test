#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${MONITOR_PROJECT_DIR:-${PROJECT_DIR:-/root/github.io-test}}"
HEALTH_URL="${MONITOR_HEALTH_URL:-https://speedycrm.ru/ready}"
MONITOR_BOT_TOKEN="${MONITOR_BOT_TOKEN:-}"
MONITOR_CHAT_ID="${MONITOR_CHAT_ID:-}"
STATE_DIR="${MONITOR_STATE_DIR:-/var/lib/aaaa-monitor}"
MAX_DISK_USED_PERCENT="${MAX_DISK_USED_PERCENT:-85}"
LOG_WINDOW="${LOG_WINDOW:-10m}"

mkdir -p "$STATE_DIR"

send_telegram() {
  local text="$1"
  if [[ -n "$MONITOR_BOT_TOKEN" && -n "$MONITOR_CHAT_ID" ]]; then
    curl --fail --silent --show-error --max-time 10 -X POST \
      "https://api.telegram.org/bot${MONITOR_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${MONITOR_CHAT_ID}" \
      --data-urlencode "text=${text}" >/dev/null || true
  fi
}

notify_state() {
  local key="$1" status="$2" message="$3"
  local state_file="$STATE_DIR/${key}.state" previous=""
  [[ -f "$state_file" ]] && previous="$(<"$state_file")"
  if [[ "$status" != "$previous" ]]; then
    printf '%s' "$status" > "$state_file"
    send_telegram "AAAA monitor: ${message}"
  fi
}

check_public_ready() {
  if curl --fail --silent --show-error --max-time 15 "$HEALTH_URL" >/dev/null; then
    notify_state public_ready ok "✅ API ready: OK"
  else
    notify_state public_ready failed "🚨 API ready недоступен: ${HEALTH_URL}"
  fi
}

check_container() {
  local service="$1"
  local container status health
  container="$(docker compose -f "$PROJECT_DIR/docker-compose.yml" ps -q "$service" 2>/dev/null || true)"
  status="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || true)"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || true)"
  if [[ "$status" == running && ( "$health" == healthy || "$health" == none ) ]]; then
    notify_state "container_${service}" ok "✅ Контейнер ${service}: running, health=${health}"
  else
    notify_state "container_${service}" failed "🚨 Контейнер ${service}: status=${status:-missing}, health=${health:-missing}"
  fi
}

check_db() {
  if docker compose -f "$PROJECT_DIR/docker-compose.yml" exec -T db pg_isready -U postgres -d crm_db >/dev/null 2>&1; then
    notify_state database ok "✅ PostgreSQL: доступна"
  else
    notify_state database failed "🚨 PostgreSQL: недоступна"
  fi
}

check_redis() {
  if docker compose -f "$PROJECT_DIR/docker-compose.yml" exec -T redis redis-cli ping 2>/dev/null | grep -qx PONG; then
    notify_state redis ok "✅ Redis: доступен"
  else
    notify_state redis failed "🚨 Redis: недоступен"
  fi
}

check_disk() {
  local used
  used="$(df -P "$PROJECT_DIR" | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
  if [[ "$used" =~ ^[0-9]+$ ]] && (( used >= MAX_DISK_USED_PERCENT )); then
    notify_state disk failed "🚨 Диск заполнен на ${used}%"
  else
    notify_state disk ok "✅ Диск: ${used:-unknown}%"
  fi
}

check_recent_errors() {
  local errors
  errors="$(docker compose -f "$PROJECT_DIR/docker-compose.yml" logs --since "$LOG_WINDOW" gym-api 2>/dev/null \
    | grep -Eai 'CRITICAL|Traceback|ERROR|ошибк|timeout|timed out|failed|failure|exception|rejected' \
    | tail -n 20 || true)"
  if [[ -z "$errors" ]]; then
    notify_state recent_errors ok "✅ Критичных ошибок API/оплат/СКУД за ${LOG_WINDOW} нет"
  else
    notify_state recent_errors failed "⚠️ Найдены ошибки API/оплат/СКУД за ${LOG_WINDOW}:\n$(printf '%s' "$errors" | tail -n 5)"
  fi
}

check_public_ready
check_container gym-api
check_container db
check_container redis
check_container nginx
check_db
check_redis
check_disk
check_recent_errors
echo "AAAA monitor OK"
