"""Общий интерфейс платёжного провайдера. Добавить новый — реализовать два метода."""

from dataclasses import dataclass
from typing import Literal

PaymentState = Literal["pending", "paid", "canceled"]


@dataclass
class PaymentLink:
    payment_id: str
    confirmation_url: str


class PaymentProvider:
    async def create_payment(self, *, amount_rub: int, description: str, return_url: str) -> PaymentLink:
        raise NotImplementedError

    async def check_status(self, payment_id: str) -> PaymentState:
        raise NotImplementedError
