#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
TARGET="$BACKUP_DIR/aaaa-$STAMP.dump"
LOCK_FILE="$BACKUP_DIR/.backup.lock"

mkdir -p "$BACKUP_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Backup already running" >&2
  exit 1
fi

cd "$PROJECT_DIR"
cleanup() { rm -f "$TARGET"; }
trap cleanup ERR

docker compose exec -T db pg_dump \
  -U "${POSTGRES_USER:-postgres}" \
  -d "${POSTGRES_DB:-crm_db}" \
  --format=custom \
  --no-owner > "$TARGET"

if [[ ! -s "$TARGET" ]]; then
  echo "Backup is empty: $TARGET" >&2
  exit 1
fi

# Проверяем архив тем же PostgreSQL-клиентом, который используется в контейнере.
docker compose exec -T db pg_restore --list < "$TARGET" >/dev/null
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'aaaa-*.dump' -mtime "+$RETENTION_DAYS" -delete
trap - ERR
echo "Backup created and verified: $TARGET"
