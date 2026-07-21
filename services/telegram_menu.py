from __future__ import annotations

import os

from aiogram import Bot, types
from loguru import logger

BASE_URL = os.getenv("BASE_URL", "https://speedycrm.ru")


def webapp_profile_url(club_id: int) -> str:
    return f"{BASE_URL}/webapp/client-cabinet?club_id={club_id}"


async def configure_profile_menu_button(bot: Bot, club_id: int) -> None:
    try:
        await bot.set_chat_menu_button(
            menu_button=types.MenuButtonWebApp(
                text="Профиль",
                web_app=types.WebAppInfo(url=webapp_profile_url(club_id)),
            )
        )
        logger.info("✅ Menu button set for club_id={}", club_id)
    except Exception as exc:
        logger.warning("⚠️ Failed to set menu button for club_id={}: {}", club_id, exc)
