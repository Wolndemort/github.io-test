from __future__ import annotations

from typing import Iterable

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from loguru import logger

bots_dict: dict[str, Bot] = {}


async def register_bot(bot_token: str, webhook_url: str, *, parse_mode: str = "HTML", drop_pending_updates: bool = True) -> Bot:
    existing = bots_dict.get(bot_token)
    if existing:
        return existing

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=parse_mode),
    )
    bots_dict[bot_token] = bot
    await bot.set_webhook(url=webhook_url, drop_pending_updates=drop_pending_updates)
    logger.info("✅ Вебхук обновлён и бот зарегистрирован token=...%s", bot_token[-8:])
    return bot


async def register_existing_bots(clubs: Iterable, base_url: str) -> None:
    for club in clubs:
        if not getattr(club, "bot_token", None):
            continue
        webhook_url = f"{base_url}/webhook/bot/{club.bot_token}"
        try:
            await register_bot(club.bot_token, webhook_url)
        except Exception as exc:
            logger.error("❌ Ошибка токена для клуба '{}': {}", getattr(club, "name", "?"), exc)


async def close_all_bots() -> None:
    for bot in list(bots_dict.values()):
        try:
            await bot.session.close()
        except Exception as exc:
            logger.warning("Не удалось закрыть сессию бота: %s", exc)
