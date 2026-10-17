from app.db import Company
from app.payments.registry import get_provider
from app.payments.stripe_provider import StripeProvider
from app.payments.yookassa import YooKassaProvider


def make_company(**kw) -> Company:
    company = Company(slug="x", name="X")
    for k, v in kw.items():
        setattr(company, k, v)
    return company


def test_no_provider_when_not_configured():
    assert get_provider(make_company()) is None


def test_yookassa_needs_shop_id_and_secret():
    assert get_provider(make_company(payment_provider="yookassa", payment_shop_id="1")) is None
    provider = get_provider(make_company(payment_provider="yookassa", payment_shop_id="1", payment_secret_key="s"))
    assert isinstance(provider, YooKassaProvider)


def test_stripe_needs_secret_key():
    assert get_provider(make_company(payment_provider="stripe")) is None
    provider = get_provider(make_company(payment_provider="stripe", payment_secret_key="sk_test_1"))
    assert isinstance(provider, StripeProvider)


def test_unknown_provider_returns_none():
    assert get_provider(make_company(payment_provider="paypal", payment_secret_key="x")) is None
