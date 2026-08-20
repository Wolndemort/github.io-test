#!/usr/bin/env bash
set -u
python3 - <<'PY'
import socket
try:
    socket.create_connection(("smtp.gmail.com", 587), 5).close()
    print("host_tcp=ok")
except Exception as exc:
    print(f"host_tcp=failed:{type(exc).__name__}")
PY
docker exec -i speedycrm_staging_api python - <<'PY'
import socket
try:
    socket.create_connection(("smtp.gmail.com", 587), 5).close()
    print("container_tcp=ok")
except Exception as exc:
    print(f"container_tcp=failed:{type(exc).__name__}")
PY
