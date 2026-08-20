from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth.web_session import require_csrf

class Redis:
    def __init__(self, value): self.value = value
    async def get(self, key): return self.value

def request(token=None):
    return SimpleNamespace(cookies={"speedycrm_web_session": "sid"}, headers={"x-csrf-token": token} if token else {})

@pytest.mark.asyncio
async def test_mutation_gate_rejects_missing_csrf():
    with pytest.raises(HTTPException) as error:
        await require_csrf(Redis('{"csrf_token":"good"}'), request())
    assert error.value.status_code == 403
    assert error.value.detail["code"] == "csrf_failed"

@pytest.mark.asyncio
async def test_mutation_gate_accepts_matching_csrf():
    await require_csrf(Redis('{"csrf_token":"good"}'), request("good"))
