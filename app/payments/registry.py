"""Выбор платёжного провайдера по настройкам конкретной компании."""

from app.db import Company
from app.payments.base import PaymentProvider
from app.payments.stripe_provider import StripeProvider
from app.payments.yookassa import YooKassaProvider


def get_provider(company: Company) -> PaymentProvider | None:
    if company.payment_provider == "yookassa" and company.payment_shop_id and company.payment_secret_key:
        return YooKassaProvider(company.payment_shop_id, company.payment_secret_key)
    if company.payment_provider == "stripe" and company.payment_secret_key:
        return StripeProvider(company.payment_secret_key)
    return None
