from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from app import repo
from app.db import Booking, BookingStatus, init_db, make_engine, make_sessionmaker

TZ = ZoneInfo("Europe/Moscow")


@pytest_asyncio.fixture
async def sm():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    sessionmaker = make_sessionmaker(engine)
    await init_db(engine, sessionmaker)
    yield sessionmaker
    await engine.dispose()


@pytest_asyncio.fixture
async def company(sm):
    """Готовая компания с дефолтным мастером и услугами — заводится так же, как через /companies."""
    async with sm() as s:
        return await repo.create_company(s, slug="romashka", name="Ромашка", owner_telegram_id=100)


def local(hour: int) -> datetime:
    return datetime(2026, 10, 20, hour, tzinfo=TZ)


async def book(sm, company, hour: int, user_id: int = 1, **kw):
    async with sm() as s:
        services = await repo.list_services(s, company.id)
        masters = await repo.list_masters(s, company.id)
        return await repo.create_booking(
            s,
            company_id=company.id,
            master_id=kw.pop("master_id", masters[0].id),
            user_id=user_id,
            client_name="Иван",
            phone="+79991234567",
            service=services[0],  # стрижка, 60 мин
            start_at=local(hour),
            **kw,
        )


# ---------- компании и мультитенантность ----------


@pytest.mark.asyncio
async def test_create_company_seeds_master_and_services(sm, company):
    async with sm() as s:
        services = await repo.list_services(s, company.id)
        masters = await repo.list_masters(s, company.id)
        admins = await repo.list_company_admins(s, company.id)
    assert len(services) == 4
    assert len(masters) == 1
    assert admins[0].telegram_id == 100 and admins[0].is_owner


@pytest.mark.asyncio
async def test_companies_are_isolated_from_each_other(sm, company):
    async with sm() as s:
        other = await repo.create_company(s, slug="vostok", name="Восток", owner_telegram_id=200)
        romashka_services = {x.name for x in await repo.list_services(s, company.id)}
        vostok_services = {x.name for x in await repo.list_services(s, other.id)}
    assert romashka_services == vostok_services  # у обеих — дефолтный каталог
    async with sm() as s:
        romashka_ids = {x.id for x in await repo.list_services(s, company.id)}
        vostok_ids = {x.id for x in await repo.list_services(s, other.id)}
    assert romashka_ids.isdisjoint(vostok_ids)  # но это разные строки в базе


@pytest.mark.asyncio
async def test_client_session_remembers_company(sm, company):
    async with sm() as s:
        assert await repo.get_client_company(s, 555) is None
        await repo.set_client_company(s, 555, company.id)
        remembered = await repo.get_client_company(s, 555)
    assert remembered.id == company.id


@pytest.mark.asyncio
async def test_companies_for_admin_and_rights_check(sm, company):
    async with sm() as s:
        assert await repo.is_company_admin(s, company.id, 100) is True
        assert await repo.is_company_admin(s, company.id, 999) is False
        await repo.add_company_admin(s, company.id, 999)
        assert await repo.is_company_admin(s, company.id, 999) is True
        found = await repo.companies_for_admin(s, 999)
    assert [c.id for c in found] == [company.id]


# ---------- мастера и изолированные календари ----------


@pytest.mark.asyncio
async def test_each_master_has_independent_calendar(sm, company):
    async with sm() as s:
        second = await repo.add_master(s, company.id, "Анна")
        masters = await repo.list_masters(s, company.id)
    assert len(masters) == 2

    await book(sm, company, 12, master_id=masters[0].id)
    async with sm() as s:
        # то же время у второго мастера свободно
        busy_second = await repo.busy_intervals(s, second.id, date(2026, 10, 20), TZ)
    assert busy_second == []


@pytest.mark.asyncio
async def test_double_booking_same_master_is_rejected(sm, company):
    await book(sm, company, 12)
    with pytest.raises(repo.SlotTakenError):
        await book(sm, company, 12, user_id=2)


# ---------- услуги, тексты ----------


@pytest.mark.asyncio
async def test_admin_can_add_edit_and_hide_services(sm, company):
    async with sm() as s:
        new = await repo.add_service(s, company_id=company.id, name="Маникюр", duration_min=90, price=2000)
        await repo.update_service(s, new.id, price=2500, name="Маникюр + покрытие")
        visible = [x.name for x in await repo.list_services(s, company.id)]
        assert "Маникюр + покрытие" in visible

        await repo.update_service(s, new.id, active=False)
        assert "Маникюр + покрытие" not in [x.name for x in await repo.list_services(s, company.id)]
        all_names = [x.name for x in await repo.list_services(s, company.id, include_hidden=True)]
        assert "Маникюр + покрытие" in all_names
        assert (await repo.get_service(s, new.id)).price == 2500


@pytest.mark.asyncio
async def test_texts_are_per_company_with_shared_defaults(sm, company):
    async with sm() as s:
        other = await repo.create_company(s, slug="vostok2", name="Восток", owner_telegram_id=201)
        assert "Здравствуйте" in await repo.get_text(s, company.id, "welcome")
        await repo.set_text(s, company.id, "welcome", "Привет из «Ромашки»!")
        assert await repo.get_text(s, company.id, "welcome") == "Привет из «Ромашки»!"
        # у другой компании текст не поменялся
        assert "Здравствуйте" in await repo.get_text(s, other.id, "welcome")
        with pytest.raises(ValueError):
            await repo.set_text(s, company.id, "unknown", "x")


# ---------- записи, отмена ----------


@pytest.mark.asyncio
async def test_cancel_frees_slot_and_checks_owner(sm, company):
    b = await book(sm, company, 12, user_id=1)
    async with sm() as s:
        assert await repo.cancel_booking(s, b.id, user_id=999) is None  # чужая запись
        assert await repo.cancel_booking(s, b.id, user_id=1) is not None
        masters = await repo.list_masters(s, company.id)
        assert await repo.busy_intervals(s, masters[0].id, date(2026, 10, 20), TZ) == []
    await book(sm, company, 12, user_id=2)  # слот снова свободен


@pytest.mark.asyncio
async def test_user_upcoming_hides_past_and_is_scoped_to_company(sm, company):
    await book(sm, company, 11)
    await book(sm, company, 15)
    async with sm() as s:
        other = await repo.create_company(s, slug="vostok3", name="Восток", owner_telegram_id=202)
        upcoming = await repo.user_upcoming(s, company.id, 1, now=local(12))
        other_company_bookings = await repo.user_upcoming(s, other.id, 1, now=local(10))
    assert [repo.as_utc(b.start_at) for b in upcoming] == [local(15).astimezone(UTC)]
    assert other_company_bookings == []


# ---------- оплата: удержание слота и просрочка ----------


@pytest.mark.asyncio
async def test_awaiting_payment_booking_holds_the_slot(sm, company):
    await book(sm, company, 12, status=BookingStatus.awaiting_payment)
    with pytest.raises(repo.SlotTakenError):
        await book(sm, company, 12, user_id=2)


@pytest.mark.asyncio
async def test_confirm_payment_moves_booking_to_active(sm, company):
    b = await book(sm, company, 12, status=BookingStatus.awaiting_payment)
    async with sm() as s:
        confirmed = await repo.confirm_payment(s, b.id)
        assert confirmed.status == BookingStatus.active
        # повторное подтверждение — уже не awaiting_payment, возвращает None
        assert await repo.confirm_payment(s, b.id) is None


@pytest.mark.asyncio
async def test_expire_unpaid_releases_the_slot(sm, company):
    b = await book(sm, company, 12, status=BookingStatus.awaiting_payment)
    async with sm() as s:
        booking = await s.get(Booking, b.id)
        booking.created_at = datetime.now(UTC) - timedelta(minutes=20)
        await s.commit()
        expired = await repo.expire_unpaid(s, hold_minutes=15)
    assert [e.id for e in expired] == [b.id]
    await book(sm, company, 12, user_id=2)  # слот снова свободен


@pytest.mark.asyncio
async def test_company_stats_counts_upcoming_and_top_services(sm, company):
    await book(sm, company, 11)
    await book(sm, company, 15)
    async with sm() as s:
        stats = await repo.company_stats(s, company.id)
    assert stats["upcoming_count"] == 2
    assert stats["top_services"][0][0] == "Стрижка"
    assert stats["unique_clients"] == 1


# ---------- чистые функции ----------


def test_parse_day():
    from app.admin_utils import parse_day

    today = date(2026, 12, 20)
    assert parse_day("25.12", today) == date(2026, 12, 25)
    assert parse_day("05.01", today) == date(2027, 1, 5)
    assert parse_day("2026-11-03", today) == date(2026, 11, 3)
    assert parse_day("ерунда", today) is None


def test_parse_int():
    from app.admin_utils import parse_int

    assert parse_int("1 500", min_value=0, max_value=10_000) == 1500
    assert parse_int("abc", min_value=0, max_value=10) is None
    assert parse_int("2", min_value=5, max_value=10) is None


def test_parse_schedule():
    from app.admin_utils import parse_schedule

    assert parse_schedule("9-21 45 0,6") == {
        "work_start_hour": 9,
        "work_end_hour": 21,
        "slot_minutes": 45,
        "days_off": [0, 6],
    }
    assert parse_schedule("10-20 60 -") == {
        "work_start_hour": 10,
        "work_end_hour": 20,
        "slot_minutes": 60,
        "days_off": [],
    }
    assert parse_schedule("бред") is None
    assert parse_schedule("25-30 60 0") is None  # часы вне диапазона суток


def test_slugify():
    from app.admin_utils import slugify

    assert slugify("Салон Ромашка") == "salon_romashka"
    assert slugify("") == "company"
