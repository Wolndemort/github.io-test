#!/bin/sh
set -eu

/docker-entrypoint.sh nginx -g 'daemon off;' &
nginx_pid=$!
previous=""

reload_on_exit() {
  kill -TERM "$nginx_pid" 2>/dev/null || true
  wait "$nginx_pid" 2>/dev/null || true
}
trap reload_on_exit INT TERM

while kill -0 "$nginx_pid" 2>/dev/null; do
  current=$(sha256sum /etc/letsencrypt/live/speedycrm.ru/fullchain.pem 2>/dev/null | awk '{print $1}' || true)
  if [ -n "$current" ] && [ -n "$previous" ] && [ "$current" != "$previous" ]; then
    nginx -s reload
  fi
  previous="$current"
  sleep 30
done

wait "$nginx_pid"
