from types import SimpleNamespace

import pytest

import scripts.smoke_check as smoke_check


class DummyResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class DummyClient:
    def __init__(self, mapping):
        self.mapping = mapping

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, path):
        return DummyResponse(self.mapping.get((method, path), 500))


@pytest.mark.asyncio
async def test_smoke_check_passes(monkeypatch):
    mapping = {
        ("GET", "/health"): 200,
        ("GET", "/ready"): 200,
        ("GET", "/webapp/client-cabinet?club_id=1"): 401,
        ("GET", "/webapp/biometric-pass?club_id=1&user_id=1"): 401,
    }
    monkeypatch.setattr(smoke_check.httpx, "AsyncClient", lambda **kwargs: DummyClient(mapping))
    rc = await smoke_check.run("http://test")
    assert rc == 0


@pytest.mark.asyncio
async def test_smoke_check_fails_on_bad_status(monkeypatch):
    mapping = {
        ("GET", "/health"): 500,
        ("GET", "/ready"): 200,
        ("GET", "/webapp/client-cabinet?club_id=1"): 401,
        ("GET", "/webapp/biometric-pass?club_id=1&user_id=1"): 401,
    }
    monkeypatch.setattr(smoke_check.httpx, "AsyncClient", lambda **kwargs: DummyClient(mapping))
    rc = await smoke_check.run("http://test")
    assert rc == 1
