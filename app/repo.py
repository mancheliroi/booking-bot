"""Работа с базой данных."""

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import (
    DEFAULT_MASTER_NAME,
    DEFAULT_SERVICES,
    EDITABLE_TEXTS,
    Booking,
    BookingStatus,
    BotText,
    ClientSession,
    Company,
    CompanyAdmin,
    Master,
    Service,
)
from app.slots import Interval, overlaps


class SlotTakenError(Exception):
    """Слот заняли, пока клиент оформлял запись."""


def as_utc(dt: datetime) -> datetime:
    # SQLite возвращает время без часового пояса — считаем его UTC
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


# ---------- компании ----------


async def create_company(session: AsyncSession, *, slug: str, name: str, owner_telegram_id: int) -> Company:
    company = Company(slug=slug, name=name)
    session.add(company)
    await session.flush()  # нужен company.id для дочерних записей

    session.add(CompanyAdmin(company_id=company.id, telegram_id=owner_telegram_id, is_owner=True))
    session.add(Master(company_id=company.id, name=DEFAULT_MASTER_NAME))
    session.add_all(
        Service(company_id=company.id, name=n, duration_min=d, price=p) for n, d, p in DEFAULT_SERVICES
    )
    await session.commit()
    await session.refresh(company)
    return company


async def get_company(session: AsyncSession, company_id: int) -> Company | None:
    return await session.get(Company, company_id)


async def get_company_by_slug(session: AsyncSession, slug: str) -> Company | None:
    return await session.scalar(select(Company).where(Company.slug == slug))


async def list_companies(session: AsyncSession) -> list[Company]:
    return list(await session.scalars(select(Company).order_by(Company.id)))


async def set_company_active(session: AsyncSession, company_id: int, active: bool) -> Company | None:
    company = await session.get(Company, company_id)
    if company is None:
        return None
    company.is_active = active
    await session.commit()
    return company


async def set_company_schedule(session: AsyncSession, company_id: int, **changes) -> Company | None:
    company = await session.get(Company, company_id)
    if company is None:
        return None
    for field, value in changes.items():
        if field == "days_off":
            company.days_off = value
        else:
            setattr(company, field, value)
    await session.commit()
    return company


async def set_company_payment(
    session: AsyncSession,
    company_id: int,
    *,
    provider: str | None = None,
    shop_id: str | None = None,
    secret_key: str | None = None,
) -> Company | None:
    company = await session.get(Company, company_id)
    if company is None:
        return None
    if provider is not None:
        company.payment_provider = provider
    if shop_id is not None:
        company.payment_shop_id = shop_id
    if secret_key is not None:
        company.payment_secret_key = secret_key
    await session.commit()
    return company


async def toggle_prepayment(session: AsyncSession, company_id: int) -> Company | None:
    company = await session.get(Company, company_id)
    if company is None:
        return None
    company.require_prepayment = not company.require_prepayment
    await session.commit()
    return company


# ---------- админы компаний ----------


async def companies_for_admin(session: AsyncSession, telegram_id: int) -> list[Company]:
    stmt = (
        select(Company)
        .join(CompanyAdmin, CompanyAdmin.company_id == Company.id)
        .where(CompanyAdmin.telegram_id == telegram_id)
        .order_by(Company.id)
    )
    return list(await session.scalars(stmt))


async def is_company_admin(session: AsyncSession, company_id: int, telegram_id: int) -> bool:
    stmt = select(CompanyAdmin.id).where(
        CompanyAdmin.company_id == company_id, CompanyAdmin.telegram_id == telegram_id
    )
    return await session.scalar(stmt) is not None


async def list_company_admins(session: AsyncSession, company_id: int) -> list[CompanyAdmin]:
    stmt = select(CompanyAdmin).where(CompanyAdmin.company_id == company_id).order_by(CompanyAdmin.id)
    return list(await session.scalars(stmt))


async def company_admin_ids(session: AsyncSession, company_id: int) -> list[int]:
    stmt = select(CompanyAdmin.telegram_id).where(CompanyAdmin.company_id == company_id)
    return list(await session.scalars(stmt))


async def add_company_admin(session: AsyncSession, company_id: int, telegram_id: int) -> CompanyAdmin:
    existing = await session.scalar(
        select(CompanyAdmin).where(CompanyAdmin.company_id == company_id, CompanyAdmin.telegram_id == telegram_id)
    )
    if existing is not None:
        return existing
    admin = CompanyAdmin(company_id=company_id, telegram_id=telegram_id)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    return admin


async def remove_company_admin(session: AsyncSession, company_id: int, telegram_id: int) -> bool:
    admin = await session.scalar(
        select(CompanyAdmin).where(CompanyAdmin.company_id == company_id, CompanyAdmin.telegram_id == telegram_id)
    )
    if admin is None:
        return False
    await session.delete(admin)
    await session.commit()
    return True


# ---------- клиентская сессия (в какой компании сейчас клиент) ----------


async def set_client_company(session: AsyncSession, telegram_id: int, company_id: int) -> None:
    row = await session.get(ClientSession, telegram_id)
    if row is None:
        session.add(ClientSession(telegram_id=telegram_id, company_id=company_id))
    else:
        row.company_id = company_id
    await session.commit()


async def get_client_company(session: AsyncSession, telegram_id: int) -> Company | None:
    row = await session.get(ClientSession, telegram_id)
    if row is None:
        return None
    return await session.get(Company, row.company_id)


async def broadcast_targets(session: AsyncSession, company_id: int) -> list[int]:
    """Все, кто хоть раз открывал бота по ссылке этой компании."""
    stmt = select(ClientSession.telegram_id).where(ClientSession.company_id == company_id)
    return list(await session.scalars(stmt))


# ---------- мастера ----------


async def list_masters(session: AsyncSession, company_id: int, *, include_hidden: bool = False) -> list[Master]:
    stmt = select(Master).where(Master.company_id == company_id).order_by(Master.id)
    if not include_hidden:
        stmt = stmt.where(Master.active.is_(True))
    return list(await session.scalars(stmt))


async def get_master(session: AsyncSession, master_id: int) -> Master | None:
    return await session.get(Master, master_id)


async def add_master(session: AsyncSession, company_id: int, name: str) -> Master:
    master = Master(company_id=company_id, name=name, active=True)
    session.add(master)
    await session.commit()
    await session.refresh(master)
    return master


MASTER_FIELDS = {"name", "active"}


async def update_master(session: AsyncSession, master_id: int, **changes) -> Master | None:
    unknown = set(changes) - MASTER_FIELDS
    if unknown:
        raise ValueError(f"Нельзя менять поля: {unknown}")
    master = await session.get(Master, master_id)
    if master is None:
        return None
    for field, value in changes.items():
        setattr(master, field, value)
    await session.commit()
    return master


# ---------- услуги ----------


async def list_services(session: AsyncSession, company_id: int, *, include_hidden: bool = False) -> list[Service]:
    stmt = select(Service).where(Service.company_id == company_id).order_by(Service.id)
    if not include_hidden:
        stmt = stmt.where(Service.active.is_(True))
    return list(await session.scalars(stmt))


async def get_service(session: AsyncSession, service_id: int) -> Service | None:
    return await session.get(Service, service_id)


SERVICE_FIELDS = {"name", "price", "duration_min", "active"}


async def add_service(session: AsyncSession, *, company_id: int, name: str, duration_min: int, price: int) -> Service:
    service = Service(company_id=company_id, name=name, duration_min=duration_min, price=price, active=True)
    session.add(service)
    await session.commit()
    await session.refresh(service)
    return service


async def update_service(session: AsyncSession, service_id: int, **changes) -> Service | None:
    unknown = set(changes) - SERVICE_FIELDS
    if unknown:
        raise ValueError(f"Нельзя менять поля: {unknown}")
    service = await session.get(Service, service_id)
    if service is None:
        return None
    for field, value in changes.items():
        setattr(service, field, value)
    await session.commit()
    return service


# ---------- тексты бота ----------


async def get_text(session: AsyncSession, company_id: int, key: str) -> str:
    row = await session.get(BotText, (company_id, key))
    if row is not None:
        return row.value
    return EDITABLE_TEXTS[key][1]


async def set_text(session: AsyncSession, company_id: int, key: str, value: str) -> None:
    if key not in EDITABLE_TEXTS:
        raise ValueError(f"Неизвестный текст: {key}")
    row = await session.get(BotText, (company_id, key))
    if row is None:
        session.add(BotText(company_id=company_id, key=key, value=value))
    else:
        row.value = value
    await session.commit()


# ---------- записи ----------

# Удержание слота на время оплаты тоже считается «занятым», но протухшие
# холды (клиент передумал оплачивать) не должны блокировать время навечно —
# их отфильтровывает фоновая задача (repo.expire_unpaid), а здесь это
# подстраховка на случай, если она ещё не успела пройти.
_HOLD_SAFETY_MINUTES = 30


async def _active_between(session: AsyncSession, master_id: int, start: datetime, end: datetime) -> list[Booking]:
    hold_cutoff = datetime.now(UTC) - timedelta(minutes=_HOLD_SAFETY_MINUTES)
    stmt = (
        select(Booking)
        .where(
            Booking.master_id == master_id,
            Booking.start_at < end.astimezone(UTC),
            Booking.end_at > start.astimezone(UTC),
        )
        .where(
            (Booking.status == BookingStatus.active)
            | ((Booking.status == BookingStatus.awaiting_payment) & (Booking.created_at >= hold_cutoff))
        )
        .order_by(Booking.start_at)
    )
    return list(await session.scalars(stmt))


def _day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=tz)
    return start, start + timedelta(days=1)


async def busy_intervals(session: AsyncSession, master_id: int, day: date, tz: ZoneInfo) -> list[Interval]:
    start, end = _day_bounds(day, tz)
    bookings = await _active_between(session, master_id, start, end)
    return [(as_utc(b.start_at), as_utc(b.end_at)) for b in bookings]


async def create_booking(
    session: AsyncSession,
    *,
    company_id: int,
    master_id: int,
    user_id: int,
    client_name: str,
    phone: str,
    service: Service,
    start_at: datetime,
    status: BookingStatus = BookingStatus.active,
) -> Booking:
    end_at = start_at + timedelta(minutes=service.duration_min)
    # Повторная проверка прямо перед сохранением: слот могли занять параллельно
    for b in await _active_between(session, master_id, start_at, end_at):
        if overlaps((start_at, end_at), (as_utc(b.start_at), as_utc(b.end_at))):
            raise SlotTakenError

    booking = Booking(
        company_id=company_id,
        master_id=master_id,
        user_id=user_id,
        client_name=client_name,
        phone=phone,
        service_id=service.id,
        price=service.price,
        start_at=start_at.astimezone(UTC),
        end_at=end_at.astimezone(UTC),
        status=status,
    )
    session.add(booking)
    await session.commit()
    await session.refresh(booking)
    return booking


async def get_booking(session: AsyncSession, booking_id: int) -> Booking | None:
    return await session.get(Booking, booking_id)


async def set_booking_payment(session: AsyncSession, booking_id: int, *, provider: str, payment_id: str) -> None:
    booking = await session.get(Booking, booking_id)
    if booking is not None:
        booking.payment_provider = provider
        booking.payment_id = payment_id
        await session.commit()


async def confirm_payment(session: AsyncSession, booking_id: int) -> Booking | None:
    booking = await session.get(Booking, booking_id)
    if booking is None or booking.status != BookingStatus.awaiting_payment:
        return None
    booking.status = BookingStatus.active
    await session.commit()
    return booking


async def pending_payments(session: AsyncSession) -> list[Booking]:
    stmt = select(Booking).where(
        Booking.status == BookingStatus.awaiting_payment, Booking.payment_id.is_not(None)
    )
    return list(await session.scalars(stmt))


async def expire_unpaid(session: AsyncSession, hold_minutes: int) -> list[Booking]:
    cutoff = datetime.now(UTC) - timedelta(minutes=hold_minutes)
    stmt = select(Booking).where(Booking.status == BookingStatus.awaiting_payment, Booking.created_at < cutoff)
    expired = list(await session.scalars(stmt))
    for b in expired:
        b.status = BookingStatus.cancelled
    if expired:
        await session.commit()
    return expired


async def user_upcoming(session: AsyncSession, company_id: int, user_id: int, now: datetime) -> list[Booking]:
    stmt = (
        select(Booking)
        .where(
            Booking.company_id == company_id,
            Booking.user_id == user_id,
            Booking.status.in_((BookingStatus.active, BookingStatus.awaiting_payment)),
            Booking.start_at > now.astimezone(UTC),
        )
        .order_by(Booking.start_at)
    )
    return list(await session.scalars(stmt))


async def bookings_for_day(session: AsyncSession, company_id: int, day: date, tz: ZoneInfo) -> list[Booking]:
    start, end = _day_bounds(day, tz)
    stmt = (
        select(Booking)
        .where(
            Booking.company_id == company_id,
            Booking.status == BookingStatus.active,
            Booking.start_at < end.astimezone(UTC),
            Booking.end_at > start.astimezone(UTC),
        )
        .order_by(Booking.start_at)
    )
    return list(await session.scalars(stmt))


async def cancel_booking(session: AsyncSession, booking_id: int, user_id: int | None = None) -> Booking | None:
    """Отменяет запись. Если передан user_id — только свою (для клиентов)."""
    booking = await session.get(Booking, booking_id)
    if booking is None or booking.status not in (BookingStatus.active, BookingStatus.awaiting_payment):
        return None
    if user_id is not None and booking.user_id != user_id:
        return None
    booking.status = BookingStatus.cancelled
    await session.commit()
    return booking


# ---------- статистика ----------


async def company_stats(session: AsyncSession, company_id: int) -> dict:
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    active_stmt = select(Booking).where(Booking.company_id == company_id, Booking.status == BookingStatus.active)
    all_active = list(await session.scalars(active_stmt))

    upcoming = [b for b in all_active if as_utc(b.start_at) > now]
    created_this_week = [b for b in all_active if as_utc(b.created_at) >= week_ago]
    recent_month = [b for b in all_active if as_utc(b.created_at) >= month_ago]

    top_services = Counter(b.service.name for b in recent_month).most_common(5)
    revenue_month = sum(b.price for b in recent_month)
    unique_clients = len({b.user_id for b in all_active})

    return {
        "upcoming_count": len(upcoming),
        "new_this_week": len(created_this_week),
        "top_services": top_services,
        "revenue_month": revenue_month,
        "unique_clients": unique_clients,
    }
