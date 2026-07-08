import hashlib
import httpx
import logging

logger = logging.getLogger("uvicorn.error")

class TBankClient:
    def __init__(self, terminal_key: str, secret_key: str, notification_url: str):
        """Принимаем ключи напрямую, чтобы не зависеть от скрытых файлов конфигурации"""
        self.terminal_key = terminal_key
        self.secret_key = secret_key
        self.notification_url = notification_url
        # КРИТИЧНО: Официальный URL Т-Кассы для API запросов всегда restapi.tinkoff.ru/v2
        self.base_url = "https://tinkoff.ru"

    def _generate_token(self, params: dict) -> str:
        """Генерация подписи Token по правилам Т-Банка"""
        sign_params = params.copy()
        sign_params["Password"] = self.secret_key
        sign_params.pop("Receipt", None)
        sign_params.pop("DATA", None)
        sign_params.pop("Shops", None)

        sorted_values = [str(sign_params[key]) for key in sorted(sign_params.keys()) if sign_params[key] is not None]
        source_string = "".join(sorted_values)
        return hashlib.sha256(source_string.encode("utf-8")).hexdigest()

    async def init_payment(self, order_id: str, amount_kopecks: int, user_id: int) -> dict:
        """Инициализация первой оплаты с флагом рекуррента (Init)"""
        url = f"{self.base_url}/Init"
        payload = {
            "TerminalKey": self.terminal_key,
            "Amount": amount_kopecks,
            "OrderId": order_id,
            "Recurrent": "Y",
            "NotificationURL": self.notification_url,
            "DATA": {"user_id": user_id}
        }
        payload["Token"] = self._generate_token(payload)

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload, timeout=10.0)
                return response.json()
            except Exception as e:
                logger.error(f"🚨 Ошибка Init запроса: {e}")
                return {"Success": False, "Message": str(e)}

    async def charge_payment(self, order_id: str, amount_kopecks: int, rebill_id: str) -> dict:
        """Автосписание по кнопке/крону (Charge)"""
        url = f"{self.base_url}/Charge"
        payload = {
            "TerminalKey": self.terminal_key,
            "Amount": amount_kopecks,
            "OrderId": order_id,
            "RebillId": rebill_id
        }
        payload["Token"] = self._generate_token(payload)

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload, timeout=10.0)
                return response.json()
            except Exception as e:
                logger.error(f"🚨 Ошибка Charge запроса: {e}")
                return {"Success": False, "Message": str(e)}
