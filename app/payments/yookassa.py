"""Приём платежей через ЮKassa (https://yookassa.ru/developers/api).

Публичный вебхук не поднимаем: фоновая задача в app.scheduler сама
периодически опрашивает статус платежа. Это проще разворачивать — не нужен
отдельный домен и SSL под каждого нового клиента бота.
"""

import base64
import uuid

import httpx

from app.payments.base import PaymentLink, PaymentProvider, PaymentState

API_URL = "https://api.yookassa.ru/v3"

_STATUS_MAP: dict[str, PaymentState] = {
    "pending": "pending",
    "waiting_for_capture": "pending",
    "succeeded": "paid",
    "canceled": "canceled",
}


class YooKassaProvider(PaymentProvider):
    def __init__(self, shop_id: str, secret_key: str) -> None:
        self.shop_id = shop_id
        self.secret_key = secret_key

    def _headers(self, *, idempotent: bool = False) -> dict:
        token = base64.b64encode(f"{self.shop_id}:{self.secret_key}".encode()).decode()
        headers = {"Authorization": f"Basic {token}"}
        if idempotent:
            headers["Idempotence-Key"] = str(uuid.uuid4())
        return headers

    async def create_payment(self, *, amount_rub: int, description: str, return_url: str) -> PaymentLink:
        payload = {
            "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": return_url},
            "capture": True,
            "description": description[:128],
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{API_URL}/payments", json=payload, headers=self._headers(idempotent=True))
            resp.raise_for_status()
            data = resp.json()
        return PaymentLink(payment_id=data["id"], confirmation_url=data["confirmation"]["confirmation_url"])

    async def check_status(self, payment_id: str) -> PaymentState:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{API_URL}/payments/{payment_id}", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        return _STATUS_MAP.get(data["status"], "pending")
