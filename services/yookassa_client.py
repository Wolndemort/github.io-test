# yookassa_client.py
import httpx
import uuid
from typing import Optional
from loguru import logger  # Оставляем только loguru — он красивый и удобный


class YooKassaClient:
    def __init__(self, shop_id: str, secret_key: str, proxy_url: Optional[str] = None):
        """
        Инициализация клиента ЮKassa. Ключи передаются динамически для каждого клуба.
        """
        self.shop_id = shop_id
        self.secret_key = secret_key

        # Базовый URL для работы с официальным API v3
        self.base_url = "https://api.yookassa.ru/v3/payments"

        # ЮKassa требует стандартную HTTP Basic Auth (Логин = shop_id, Пароль = secret_key)
        self.auth = (self.shop_id, self.secret_key)

        # Настройка прокси для обхода геоблокировок (Критично для сервера в Вене!)
        self.mounts = None
        if proxy_url:
            self.mounts = {
                "all://api.yookassa.ru": httpx.AsyncHTTPTransport(proxy=proxy_url)
            }
            logger.info("🌐 Трафик к API ЮKassa успешно направлен через РФ-прокси.")

    async def init_payment(
            self,
            order_id: str,
            amount_kopecks: int,
            user_id: int,
            bot_username: str,
            user_email: Optional[str] = None,  # 🆕 Добавлено для чека
            user_phone: Optional[str] = None,  # 🆕 Добавлено для чека
            vat_code: int = 1,  # 🆕 Добавлено: 1 — без НДС (самый частый для ботов)
            payment_method_type: str = "bank_card"
    ) -> dict:
        """
        Создание первой оплаты.
        Передаем save_payment_method=True, чтобы ЮKassa сохранила карту для подписки.
        """
        if not order_id or len(order_id) > 50 or not isinstance(amount_kopecks, int) or amount_kopecks <= 0:
            return {"Success": False, "Message": "Некорректные параметры платежа"}
        if not self.shop_id or not self.secret_key:
            return {"Success": False, "Message": "Платежный магазин не настроен"}
        url = self.base_url
        payment_method_type = (payment_method_type or "bank_card").strip().lower()
        if payment_method_type not in {"bank_card", "sbp"}:
            return {"Success": False, "Message": f"Неподдерживаемый способ оплаты: {payment_method_type}"}

        # Переводим копейки из твоей модели БД в рубли (формат "3500.00")
        amount_rub = f"{amount_kopecks / 100:.2f}"

        # Очищаем юзернейм от собаки, если админ передал его как @my_bot
        clean_bot_username = bot_username.replace("@", "")

        # 🆕 Формируем обязательный для боевого режима объект чека (54-ФЗ)
        customer = {}
        if user_email:
            customer["email"] = user_email
        if user_phone:
            # Исправлено: используем lambda, чтобы избежать ошибки с методом self
            clean_phone = "".join(filter(lambda x: x.isdigit(), user_phone))
            customer["phone"] = f"+{clean_phone}" if not user_phone.startswith("+") else user_phone

        # Если не передали ни email, ни телефон, ЮKassa выдаст ошибку.
        # В качестве фолбека можно использовать заглушку, но лучше собирать email/телефон у юзера.
        if not customer:
            logger.warning(f"⚠️ Для платежа {order_id} не передан контакт юзера. Применен фолбек email.")
            customer["email"] = "no-email@merchant.ru"

        payload = {
            "amount": {
                "value": amount_rub,
                "currency": "RUB"
            },
            "capture": True,  # Списывать деньги сразу (без двухэтапной заморозки)
            "confirmation": {
                "type": "redirect",
                "return_url": f"https://t.me/{clean_bot_username}?start=check_{order_id}"
            },
            "metadata": {
                "order_id": order_id,
                "user_id": user_id
            },
            "description": "Первоначальный взнос и привязка карты для регулярной подписки",

            # 🆕 ДОБАВЛЕН ОБЪЕКТ ЧЕКА ДЛЯ ФИСКАЛИЗАЦИИ
            "receipt": {
                "customer": customer,
                "items": [
                    {
                        "description": "Доступ к закрытому клубу (подписка)",
                        "quantity": "1.00",
                        "amount": {
                            "value": amount_rub,
                            "currency": "RUB"
                        },
                        "vat_code": vat_code,  # 1 — Без НДС, 2 — 0%, 3 — 10%, 4 — 20%
                        "payment_mode": "full_payment",
                        "payment_subject": "service"
                    }
                ]
            }
        }

        if payment_method_type == "sbp":
            payload["payment_method_data"] = {"type": "sbp"}
            payload["description"] = "Оплата через СБП"
        else:
            payload["save_payment_method"] = True  # ‼️ КЛЮЧЕВОЙ ФЛАГ ДЛЯ SAAS (сохранить карту)

        # ЮKassa требует уникальный Idempotence-Key для каждого запроса создания платежа
        headers = {
            # Повтор запроса того же заказа должен вернуть тот же платеж,
            # а не создать второй.
            "Idempotence-Key": order_id,
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient(mounts=self.mounts, auth=self.auth) as client:
            try:
                response = await client.post(url, json=payload, headers=headers, timeout=10.0)
                res_json = response.json()

                # ЮKassa при успешном создании платежа возвращает HTTP 201 Created
                if response.status_code == 200 or response.status_code == 201:
                    logger.info(f"✅ Ссылка на оплату создана. ID Платежа ЮKassa: {res_json['id']}")
                    return {
                        "Success": True,
                        "PaymentId": res_json["id"],
                        "PaymentURL": res_json["confirmation"]["confirmation_url"]
                    }
                else:
                    logger.error(f"🚨 Ошибка ЮKassa API ({response.status_code}): {res_json}")
                    return {"Success": False, "Message": res_json.get("description", "Ошибка создания платежа")}

            except Exception as e:
                # ⚡ ИСПРАВЛЕНО: logger.exception принудительно выведет в логи Docker глубокий трассировочный лог ошибки сети!
                logger.exception(f"🚨 КРИТИЧЕСКИЙ СБОЙ NET IO ЗАПРОСА К ЮКАССЕ: {repr(e)}")
                return {"Success": False, "Message": str(e)}

    async def charge_payment(self, order_id: str, amount_kopecks: int, payment_method_id: str, club_name: str) -> dict:
        """
        Автосписание без участия пользователя (Рекуррентный платеж по крону).
        """
        if not order_id or len(order_id) > 50 or not isinstance(amount_kopecks, int) or amount_kopecks <= 0:
            return {"Success": False, "Message": "Некорректные параметры платежа"}
        if not payment_method_id or not self.shop_id or not self.secret_key:
            return {"Success": False, "Message": "Платежные реквизиты не настроены"}
        url = self.base_url
        amount_rub = f"{amount_kopecks / 100:.2f}"

        payload = {
            "amount": {
                "value": amount_rub,
                "currency": "RUB"
            },
            "capture": True,
            "payment_method_id": payment_method_id,  # Передаем сохраненный ID карты (бывший rebill_id)
            "metadata": {
                "order_id": order_id
            },
            "description": f"Автопродление подписки. Клуб: {club_name}"
        }

        headers = {
            "Idempotence-Key": order_id,
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient(mounts=self.mounts, auth=self.auth) as client:
            try:
                response = await client.post(url, json=payload, headers=headers, timeout=10.0)
                res_json = response.json()

                # ⚡ ИСПРАВЛЕНО: Для автосписания (charge) ЮKassa возвращает статус-код 200 OK
                if response.status_code == 200:
                    return {
                        "Success": True,
                        "PaymentId": res_json["id"],
                        "Status": res_json["status"]  # Вернет 'succeeded' или 'pending'
                    }
                else:
                    logger.error(f"🚨 Ошибка автосписания ({response.status_code}): {res_json}")
                    return {"Success": False, "Message": res_json.get("description", "Ошибка списания")}

            except Exception as e:
                logger.error(f"🚨 Ошибка Charge запроса: {repr(e)}")
                return {"Success": False, "Message": str(e)}
