"""Работа с базой данных."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Booking, BookingStatus, Service
from app.slots import Interval, overlaps


class SlotTakenError(Exception):
    """Слот заняли, пока клиент оформлял запись."""


def as_utc(dt: datetime) -> datetime:
    # SQLite возвращает время без часового пояса — считаем его UTC
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


async def list_services(session: AsyncSession) -> list[Service]:
    return list(await session.scalars(select(Service).order_by(Service.id)))


async def get_service(session: AsyncSession, service_id: int) -> Service | None:
    return await session.get(Service, service_id)


async def _active_between(session: AsyncSession, start: datetime, end: datetime) -> list[Booking]:
    stmt = (
        select(Booking)
        .where(
            Booking.status == BookingStatus.active,
            Booking.start_at < end.astimezone(UTC),
            Booking.end_at > start.astimezone(UTC),
        )
        .order_by(Booking.start_at)
    )
    return list(await session.scalars(stmt))


def _day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=tz)
    return start, start + timedelta(days=1)


async def busy_intervals(session: AsyncSession, day: date, tz: ZoneInfo) -> list[Interval]:
    start, end = _day_bounds(day, tz)
    bookings = await _active_between(session, start, end)
    return [(as_utc(b.start_at), as_utc(b.end_at)) for b in bookings]


async def create_booking(
    session: AsyncSession,
    *,
    user_id: int,
    client_name: str,
    phone: str,
    service: Service,
    start_at: datetime,
) -> Booking:
    end_at = start_at + timedelta(minutes=service.duration_min)
    # Повторная проверка прямо перед сохранением: слот могли занять параллельно
    for b in await _active_between(session, start_at, end_at):
        if overlaps((start_at, end_at), (as_utc(b.start_at), as_utc(b.end_at))):
            raise SlotTakenError

    booking = Booking(
        user_id=user_id,
        client_name=client_name,
        phone=phone,
        service_id=service.id,
        start_at=start_at.astimezone(UTC),
        end_at=end_at.astimezone(UTC),
    )
    session.add(booking)
    await session.commit()
    await session.refresh(booking)
    return booking


async def user_upcoming(session: AsyncSession, user_id: int, now: datetime) -> list[Booking]:
    stmt = (
        select(Booking)
        .where(
            Booking.user_id == user_id,
            Booking.status == BookingStatus.active,
            Booking.start_at > now.astimezone(UTC),
        )
        .order_by(Booking.start_at)
    )
    return list(await session.scalars(stmt))


async def bookings_for_day(session: AsyncSession, day: date, tz: ZoneInfo) -> list[Booking]:
    start, end = _day_bounds(day, tz)
    return await _active_between(session, start, end)


async def cancel_booking(session: AsyncSession, booking_id: int, user_id: int | None = None) -> Booking | None:
    """Отменяет запись. Если передан user_id — только свою (для клиентов)."""
    booking = await session.get(Booking, booking_id)
    if booking is None or booking.status != BookingStatus.active:
        return None
    if user_id is not None and booking.user_id != user_id:
        return None
    booking.status = BookingStatus.cancelled
    await session.commit()
    return booking
