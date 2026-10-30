#!/usr/bin/env python3
"""Certbot manual DNS hooks for AEZA DNS API v2.

The API token is read from a Docker secret and is never printed.  The cleanup
hook deletes only the TXT record created by the matching auth hook.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = os.environ.get("AEZA_API_BASE", "https://my.aeza.ru/api/v2").rstrip("/")
SECRET_PATH = Path(os.environ.get("AEZA_API_SECRET_FILE", "/run/secrets/aeza_api_token"))
STATE_DIR = Path(os.environ.get("AEZA_DNS_STATE_DIR", "/tmp/aeza-dns"))


def fail(message: str) -> "NoReturn":
    print(f"AEZA DNS hook: {message}", file=sys.stderr)
    raise SystemExit(1)


def token() -> str:
    try:
        value = SECRET_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        fail(f"cannot read API secret: {exc}")
    if not value:
        fail("API secret is empty")
    return value


def request(method: str, path: str, payload: dict | None = None) -> dict:
    print(f"AEZA DNS hook: {method} {path}", file=sys.stderr, flush=True)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"X-API-Key": token(), "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(f"{API_BASE}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        fail(f"AEZA API returned HTTP {exc.code} for {method} {path}")
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        fail(f"AEZA API request failed: {exc}")


def items(response: dict) -> list[dict]:
    value = response.get("items", response.get("data", []))
    return value if isinstance(value, list) else []


def zone_id(domain: str) -> int:
    # Certbot supplies *.speedycrm.ru for a wildcard challenge.
    base = domain.removeprefix("*.").rstrip(".").lower()
    for item in items(request("GET", "/domains")):
        name = str(item.get("name", item.get("domain", ""))).rstrip(".").lower()
        if name == base:
            value = item.get("id", item.get("domainId"))
            if value is not None:
                return int(value)
    fail(f"DNS zone not found for {base}")


def state_path(domain: str, validation: str) -> Path:
    digest = hashlib.sha256(f"{domain}\0{validation}".encode()).hexdigest()
    return STATE_DIR / digest


def _skip_dns_name(packet: bytes, offset: int) -> int:
    while True:
        length = packet[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += length + 1


def authoritative_txt(name: str) -> set[str]:
    labels = name.rstrip(".").split(".")
    qname = b"".join(bytes([len(label)]) + label.encode() for label in labels) + b"\0"
    query_id = os.getpid() & 0xFFFF
    packet = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", 16, 1)
    nameserver = os.environ.get("AEZA_DNS_SERVER", "a.aeza-dns.ru")
    addresses = socket.getaddrinfo(nameserver, 53, type=socket.SOCK_DGRAM)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(5)
        sock.sendto(packet, addresses[0][4])
        response = sock.recv(4096)
    _, _, _, answers, authorities, additionals = struct.unpack("!HHHHHH", response[:12])
    offset = 12
    offset = _skip_dns_name(response, offset) + 4
    values: set[str] = set()
    for _ in range(answers + authorities + additionals):
        offset = _skip_dns_name(response, offset)
        rtype, _, _, rdlength = struct.unpack("!HHIH", response[offset : offset + 10])
        offset += 10
        rdata = response[offset : offset + rdlength]
        offset += rdlength
        if rtype == 16:
            pos = 0
            chunks = []
            while pos < len(rdata):
                size = rdata[pos]
                pos += 1
                chunks.append(rdata[pos : pos + size].decode("utf-8", "replace"))
                pos += size
            values.add("".join(chunks))
    return values


def wait_for_public_dns(name: str, expected: str) -> None:
    # ACME validators use recursive resolvers, so wait until two public
    # resolvers can see the value rather than trusting only the authoritative NS.
    resolvers = (
        "https://dns.google/resolve?",
        "https://cloudflare-dns.com/dns-query?",
    )
    query = urlencode({"name": name, "type": "TXT"})
    for _ in range(60):
        try:
            visible = 0
            for endpoint in resolvers:
                req = Request(
                    f"{endpoint}{query}",
                    headers={"Accept": "application/dns-json"},
                )
                with urlopen(req, timeout=10) as response:
                    answer = json.loads(response.read().decode("utf-8")).get("Answer", [])
                values = {str(row.get("data", "")).strip('"') for row in answer}
                visible += expected in values
            if visible == len(resolvers):
                # Sleep only after the last challenge is published. This lets
                # both TXT values exist before ACME validation and avoids a
                # double wait for a certificate with apex + wildcard names.
                if os.environ.get("CERTBOT_REMAINING_CHALLENGES", "0") == "0":
                    # Let ACME's own recursive resolvers expire an older
                    # cached TXT set before validation starts (AEZA TTL 300s).
                    time.sleep(int(os.environ.get("AEZA_PROPAGATION_DELAY", "300")))
                return
        except (OSError, json.JSONDecodeError):
            pass
        time.sleep(5)
    fail(f"TXT record {name} did not propagate in time")


def auth() -> None:
    domain = os.environ.get("CERTBOT_DOMAIN", "")
    validation = os.environ.get("CERTBOT_VALIDATION", "")
    if not domain or not validation:
        fail("CERTBOT_DOMAIN or CERTBOT_VALIDATION is missing")
    record = request(
        "POST",
        f"/domains/{zone_id(domain)}/records",
        {"type": "TXT", "name": "_acme-challenge", "content": validation, "ttl": 300},
    )
    record_id = record.get("id")
    if record_id is None and isinstance(record.get("data"), dict):
        record_id = record["data"].get("id")
    if record_id is None:
        fail("AEZA did not return the created record id")
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_path(domain, validation).write_text(str(record_id), encoding="ascii")
    wait_for_public_dns(f"_acme-challenge.{domain.removeprefix('*.')}", validation)


def cleanup() -> None:
    domain = os.environ.get("CERTBOT_DOMAIN", "")
    validation = os.environ.get("CERTBOT_VALIDATION", "")
    state = state_path(domain, validation)
    if not state.exists():
        return
    record_id = state.read_text(encoding="ascii").strip()
    try:
        request("DELETE", f"/domains/{zone_id(domain)}/records/{record_id}")
    finally:
        state.unlink(missing_ok=True)


if len(sys.argv) != 2 or sys.argv[1] not in {"auth", "cleanup"}:
    fail("usage: aeza_dns.py auth|cleanup")
if sys.argv[1] == "auth":
    auth()
else:
    cleanup()
