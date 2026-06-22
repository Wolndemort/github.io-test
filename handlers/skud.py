import aiohttp
from aiogram import Router, types
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import Club

router = Router()


async def trigger_dingtian_turnstile(config: dict) -> bool:
    base_url = config.get("base_url")
    if not base_url:
        logger.error("Ошибка СКУД: отсутствует base_url в конфигурации")
        return False

    # Берем параметры, строго сверяя ключи с функцией сохранения
    relay = config.get("relay_index", 1)  # Рекомендую ставить 1 по умолчанию для Dingtian
    pulse_time = config.get("pulse_time_seconds", 1)
    timeout_val = config.get("timeout_seconds", 5)
    password = config.get("password", "")

    clean_url = base_url.rstrip("/")

    # ИСПРАВЛЕНО: Корректный URL API для китайских плат Dingtian
    url = f"{clean_url}/relay_cgi.cgi?relayno={relay}&action=pulse&time={pulse_time}"

    timeout = aiohttp.ClientTimeout(total=timeout_val)
    auth = aiohttp.BasicAuth(login="admin", password=password) if password else None

    try:
        async with aiohttp.ClientSession(timeout=timeout, auth=auth) as session:
            logger.info(f"Отправка запроса на открытие турникета: {url}")
            async with session.get(url) as response:
                if response.status == 200:
                    response_text = await response.text()
                    logger.info(f"Турникет успешно открыт. Ответ платы: {response_text.strip()}")
                    return True
                elif response.status in (401, 403):
                    # ИСПРАВЛЕНО: Опечатка в тексте лога (было 410 вместо 401)
                    logger.error(
                        f"Ошибка авторизации (401, 403) при доступе к реле по адресу {clean_url}. Проверьте пароль!")
                    return False
                else:
                    logger.error(f"Реле вернуло статус ошибки HTTP {response.status} на запрос к {clean_url}")
                    return False
    except aiohttp.ClientConnectorError:
        logger.error(
            f"Ошибка подключения. Не удалось установить соединение к реле {clean_url}. Проверьте KeenDNS и роутер")
        return False
    except aiohttp.ServerTimeoutError:
        logger.error(f"Превышено время ожидания ответа (Таймаут {timeout_val} сек) от устройства {clean_url}")
        return False
    except Exception as e:
        # ИСПРАВЛЕНО: У loguru используется exception=True вместо ecx_info=True
        logger.error(f"Непредвиденная критическая ошибка в модуле СКУД: {e}", exception=True)
        return False


async def save_and_test_turnstile(
        message: types.Message,
        session: AsyncSession,
        club: Club,
        url: str,
        password: str
) -> None:
    new_turnstile_config = {
        "enabled": True,
        "type": "dingtian_http",
        "base_url": url,
        "password": password,
        "relay_index": 1,
        "pulse_time_seconds": 1,
        "timeout_seconds": 5
    }

    # ИСПРАВЛЕНО: Заменили .settings на .club_settings
    current_settings = dict(club.club_settings) if club.club_settings else {}
    current_settings["turnstile"] = new_turnstile_config
    try:
        club.club_settings = current_settings
        # ИСПРАВЛЕНО: используем merge вместо add, чтобы обновить, а не дублировать запись
        await session.merge(club)
        await session.commit()

    except Exception as db_err:
        logger.error(f"❌ Не удалось сохранить настройки: {db_err}")
        await message.answer("❌ <b>Ошибка БД.</b> Не удалось сохранить настройки. Попробуйте позже.")
        return

    progress_msg = await message.answer(
        "✅ <i>Параметры успешно сохранены в систему.\nВыполняю проверочный запрос к оборудованию клуба...</i>",
        parse_mode="HTML"
    )

    is_device_online = await trigger_dingtian_turnstile(new_turnstile_config)
    if is_device_online:
        await progress_msg.edit_text(
            "🟢 <b>СКУД успешно подключен и активен!</b>\n\n"
            "Облачный сервер успешно связался с реле в клубе (код 200).\n"
            "Турникет должен открыться.\n\n"
            "Автоматический пропуск по QR активирован.",
            parse_mode="HTML"
        )
    else:
        await progress_msg.edit_text(
            "⚠️ <b>Параметры сохранены, но связь с клубом отсутствует!</b>\n\n"
            "Сервер записал настройки, но не смог открыть турникет.\n"
            "<b>Возможные проблемы:</b>\n"
            "1. Ошибка в адресе KeenDNS\n"
            "2. В клубе отключен роутер\n"
            "3. В Keenetic не настроен удаленный доступ к веб-приложению платы\n"
            "4. Неверный пароль от веб-панели реле\n\n"
            "<i>Пока связь не восстановится, автоматическое открытие работать не будет.</i>",
            parse_mode="HTML"
        )

