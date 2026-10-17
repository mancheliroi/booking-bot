"""Приём платежей через Stripe Checkout (https://stripe.com/docs/api/checkout/sessions).

Как и для ЮKassa, статус оплаты не ждём вебхуком — app.scheduler опрашивает
его тем же способом, что и для ЮKassa, через общий интерфейс PaymentProvider.
"""

import httpx

from app.payments.base import PaymentLink, PaymentProvider, PaymentState

API_URL = "https://api.stripe.com/v1"

_STATUS_MAP: dict[str, PaymentState] = {
    "open": "pending",
    "complete": "paid",
    "expired": "canceled",
}


class StripeProvider(PaymentProvider):
    def __init__(self, secret_key: str) -> None:
        self.secret_key = secret_key

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.secret_key}"}

    async def create_payment(self, *, amount_rub: int, description: str, return_url: str) -> PaymentLink:
        data = {
            "mode": "payment",
            "success_url": return_url,
            "cancel_url": return_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "rub",
            "line_items[0][price_data][unit_amount]": str(amount_rub * 100),
            "line_items[0][price_data][product_data][name]": description[:120],
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{API_URL}/checkout/sessions", data=data, headers=self._headers())
            resp.raise_for_status()
            payload = resp.json()
        return PaymentLink(payment_id=payload["id"], confirmation_url=payload["url"])

    async def check_status(self, payment_id: str) -> PaymentState:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{API_URL}/checkout/sessions/{payment_id}", headers=self._headers())
            resp.raise_for_status()
            payload = resp.json()
        return _STATUS_MAP.get(payload["status"], "pending")
