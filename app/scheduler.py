"""Фоновые задачи: подтверждение оплаты и снятие просроченных холдов.

Публичный вебхук под платёжные уведомления не поднимаем — это бы потребовало
отдельного домена и SSL на каждую компанию. Вместо этого раз в полминуты сами
спрашиваем у провайдера статус ещё не оплаченных записей.
"""

import asyncio
import logging

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import repo
from app.payments.registry import get_provider

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 30


async def poll_payments(bot: Bot, sessionmaker: async_sessionmaker, hold_minutes: int) -> None:
    while True:
        try:
            await _tick(bot, sessionmaker, hold_minutes)
        except Exception:
            log.exception("Сбой фоновой проверки платежей")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _notify(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text)
    except Exception:
        log.warning("Не удалось отправить сообщение %s", chat_id)


async def _tick(bot: Bot, sessionmaker: async_sessionmaker, hold_minutes: int) -> None:
    async with sessionmaker() as session:
        expired = await repo.expire_unpaid(session, hold_minutes)
    for booking in expired:
        await _notify(
            bot,
            booking.user_id,
            f"⌛ Запись №{booking.id} отменена — оплата не поступила вовремя. "
            "Можно записаться ещё раз через «Записаться».",
        )

    async with sessionmaker() as session:
        pending = await repo.pending_payments(session)
        for booking in pending:
            company = await repo.get_company(session, booking.company_id)
            provider = get_provider(company) if company else None
            if provider is None:
                continue
            try:
                state = await provider.check_status(booking.payment_id)
            except Exception:
                log.warning("Не удалось проверить платёж %s", booking.payment_id)
                continue

            if state == "paid":
                confirmed = await repo.confirm_payment(session, booking.id)
                if confirmed is None:
                    continue
                after_text = await repo.get_text(session, company.id, "booked")
                await _notify(bot, confirmed.user_id, f"✅ Оплата получена! Вы записаны.\n\n{after_text}")
                for admin_id in await repo.company_admin_ids(session, company.id):
                    await _notify(bot, admin_id, f"💳 Оплачена запись №{confirmed.id}")
            elif state == "canceled":
                await repo.cancel_booking(session, booking.id)
                await _notify(bot, booking.user_id, f"Платёж по записи №{booking.id} отменён, время снова свободно.")
