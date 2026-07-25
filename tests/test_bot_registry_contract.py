from types import SimpleNamespace

import pytest

import services.bot_registry as registry


class FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeBot:
    created = []

    def __init__(self, token, default=None):
        self.token = token
        self.default = default
        self.webhooks = []
        self.session = FakeSession()
        FakeBot.created.append(self)

    async def set_webhook(self, url, drop_pending_updates=True):
        self.webhooks.append((url, drop_pending_updates))


@pytest.mark.asyncio
async def test_register_bot_is_idempotent_and_sets_webhook_once(monkeypatch):
    monkeypatch.setattr(registry, "Bot", FakeBot)
    registry.bots_dict.clear()
    FakeBot.created.clear()

    bot = await registry.register_bot("TOKEN_1", "https://example.com/webhook/bot/TOKEN_1")
    same = await registry.register_bot("TOKEN_1", "https://example.com/webhook/bot/TOKEN_1")

    assert bot is same
    assert len(FakeBot.created) == 1
    assert bot.webhooks == [("https://example.com/webhook/bot/TOKEN_1", True)]
    assert registry.bots_dict["TOKEN_1"] is bot


@pytest.mark.asyncio
async def test_register_existing_bots_skips_empty_tokens_and_uses_base_url(monkeypatch):
    monkeypatch.setattr(registry, "Bot", FakeBot)
    registry.bots_dict.clear()
    FakeBot.created.clear()

    clubs = [
        SimpleNamespace(name="One", bot_token="TOKEN_A"),
        SimpleNamespace(name="Two", bot_token=None),
        SimpleNamespace(name="Three", bot_token="TOKEN_B"),
    ]

    await registry.register_existing_bots(clubs, "https://speedycrm.ru")

    assert "TOKEN_A" in registry.bots_dict
    assert "TOKEN_B" in registry.bots_dict
    assert all(url.startswith("https://speedycrm.ru/webhook/bot/") for bot in FakeBot.created for url, _ in bot.webhooks)

