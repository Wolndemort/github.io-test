from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message
from loguru import logger


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if user:
            message_text = getattr(event, 'text', getattr(event.event, 'text', '[не текст]'))\
                if hasattr(event, 'event') else getattr(
                event, 'text', '[не текст]')
            logger.info(f"Событие от {user.full_name} (ID: {user.id}): {message_text}")

        return await handler(event, data)
