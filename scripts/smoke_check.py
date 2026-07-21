from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class SmokeCase:
    name: str
    method: str
    path: str
    expected: tuple[int, ...]


CASES: list[SmokeCase] = [
    SmokeCase("health", "GET", "/health", (200,)),
    SmokeCase("ready", "GET", "/ready", (200,)),
    SmokeCase("webapp_cabinet_gate", "GET", "/webapp/client-cabinet?club_id=1", (401,)),
    SmokeCase("webapp_faceid_gate", "GET", "/webapp/biometric-pass?club_id=1&user_id=1", (401,)),
]


async def run(base_url: str) -> int:
    failures = 0
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        for case in CASES:
            response = await client.request(case.method, case.path)
            ok = response.status_code in case.expected
            status = "OK" if ok else "FAIL"
            print(f"[{status}] {case.name}: {response.status_code} {case.path}")
            if not ok:
                failures += 1
    return 0 if failures == 0 else 1


def main() -> int:
    base_url = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    return __import__("asyncio").run(run(base_url))


if __name__ == "__main__":
    raise SystemExit(main())
