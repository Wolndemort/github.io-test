import pytest

from services.yookassa_client import YooKassaClient


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeAsyncClient:
    response = None
    last_request = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, json, headers, timeout):
        self.__class__.last_request = {
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        }
        return self.response


@pytest.mark.asyncio
async def test_init_payment_builds_receipt_and_returns_url(monkeypatch):
    FakeAsyncClient.response = FakeResponse(
        201,
        {
            "id": "payment-1",
            "confirmation": {"confirmation_url": "https://pay.example/1"},
        },
    )
    monkeypatch.setattr(
        "services.yookassa_client.httpx.AsyncClient", FakeAsyncClient
    )

    result = await YooKassaClient("shop", "secret").init_payment(
        order_id="INIT_1",
        amount_kopecks=125000,
        user_id=42,
        bot_username="club_bot",
    )

    assert result == {
        "Success": True,
        "PaymentId": "payment-1",
        "PaymentURL": "https://pay.example/1",
    }
    payload = FakeAsyncClient.last_request["json"]
    assert payload["amount"]["value"] == "1250.00"
    assert payload["metadata"]["order_id"] == "INIT_1"
    assert payload["save_payment_method"] is True
    assert payload["receipt"]["items"][0]["amount"]["value"] == "1250.00"
    assert "payment_method_data" not in payload


@pytest.mark.asyncio
async def test_init_payment_can_build_sbp_payload(monkeypatch):
    FakeAsyncClient.response = FakeResponse(
        201,
        {
            "id": "payment-sbp",
            "confirmation": {"confirmation_url": "https://pay.example/sbp"},
        },
    )
    monkeypatch.setattr(
        "services.yookassa_client.httpx.AsyncClient", FakeAsyncClient
    )

    result = await YooKassaClient("shop", "secret").init_payment(
        order_id="SBP_1",
        amount_kopecks=7800,
        user_id=77,
        bot_username="club_bot",
        payment_method_type="sbp",
    )

    assert result == {
        "Success": True,
        "PaymentId": "payment-sbp",
        "PaymentURL": "https://pay.example/sbp",
    }
    payload = FakeAsyncClient.last_request["json"]
    assert payload["payment_method_data"] == {"type": "sbp"}
    assert "save_payment_method" not in payload
    assert payload["description"] == "Оплата через СБП"


@pytest.mark.asyncio
async def test_charge_payment_returns_bank_status(monkeypatch):
    FakeAsyncClient.response = FakeResponse(
        200, {"id": "payment-2", "status": "succeeded"}
    )
    monkeypatch.setattr(
        "services.yookassa_client.httpx.AsyncClient", FakeAsyncClient
    )

    result = await YooKassaClient("shop", "secret").charge_payment(
        order_id="REC_1",
        amount_kopecks=350000,
        payment_method_id="pm_1",
        club_name="Клуб",
    )

    assert result["Success"] is True
    assert result["Status"] == "succeeded"
    assert FakeAsyncClient.last_request["json"]["metadata"]["order_id"] == "REC_1"
