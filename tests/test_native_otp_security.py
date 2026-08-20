import pytest

from auth.native_auth import allow_otp_request, consume_otp, issue_otp


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.counters = {}
        self.expiries = {}

    async def hset(self, key, mapping):
        self.hashes[key] = {str(k): str(v) for k, v in mapping.items()}

    async def expire(self, key, seconds):
        self.expiries[key] = seconds

    async def hgetall(self, key):
        return {k.encode(): v.encode() for k, v in self.hashes.get(key, {}).items()}

    async def hincrby(self, key, field, amount):
        self.hashes[key][field] = str(int(self.hashes[key].get(field, 0)) + amount)

    async def delete(self, key):
        self.hashes.pop(key, None)

    async def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]


@pytest.mark.asyncio
async def test_otp_is_single_use_and_attempt_bounded(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    code = await issue_otp(redis, "User@Example.com", 2)
    assert redis.expiries["web_native_otp:login:2:user@example.com"] == 600
    assert await consume_otp(redis, "user@example.com", 2, "000000") is False
    assert await consume_otp(redis, "user@example.com", 2, code) is True
    assert await consume_otp(redis, "user@example.com", 2, code) is False


@pytest.mark.asyncio
async def test_otp_request_limit_applies_to_email_and_ip_club():
    redis = FakeRedis()
    for _ in range(3):
        assert await allow_otp_request(redis, "user@example.com", 2, "127.0.0.1") is True
    assert await allow_otp_request(redis, "user@example.com", 2, "127.0.0.1") is False


@pytest.mark.asyncio
async def test_unknown_or_expired_otp_cannot_be_consumed():
    redis = FakeRedis()
    assert await consume_otp(redis, "missing@example.com", 2, "123456") is False
    await issue_otp(redis, "user@example.com", 2)
    await redis.delete("web_native_otp:login:2:user@example.com")
    assert await consume_otp(redis, "user@example.com", 2, "123456") is False
