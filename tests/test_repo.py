from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from app import repo
from app.db import init_db, make_engine, make_sessionmaker

TZ = ZoneInfo("Europe/Moscow")


@pytest_asyncio.fixture
async def sm():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    sessionmaker = make_sessionmaker(engine)
    await init_db(engine, sessionmaker)
    yield sessionmaker
    await engine.dispose()


def local(hour: int) -> datetime:
    return datetime(2026, 10, 20, hour, tzinfo=TZ)


async def book(sm, hour: int, user_id: int = 1):
    async with sm() as s:
        service = (await repo.list_services(s))[0]  # стрижка, 60 мин
        return await repo.create_booking(
            s, user_id=user_id, client_name="Иван", phone="+79991234567", service=service, start_at=local(hour)
        )


@pytest.mark.asyncio
async def test_default_services_seeded(sm):
    async with sm() as s:
        services = await repo.list_services(s)
    assert len(services) == 4


@pytest.mark.asyncio
async def test_booking_appears_in_busy_and_day_list(sm):
    await book(sm, 12)
    async with sm() as s:
        busy = await repo.busy_intervals(s, date(2026, 10, 20), TZ)
        day = await repo.bookings_for_day(s, date(2026, 10, 20), TZ)
    assert busy == [(local(12).astimezone(UTC), local(13).astimezone(UTC))]
    assert len(day) == 1 and day[0].service.name == "Стрижка"


@pytest.mark.asyncio
async def test_double_booking_is_rejected(sm):
    await book(sm, 12)
    with pytest.raises(repo.SlotTakenError):
        await book(sm, 12, user_id=2)


@pytest.mark.asyncio
async def test_cancel_frees_slot_and_checks_owner(sm):
    b = await book(sm, 12, user_id=1)
    async with sm() as s:
        assert await repo.cancel_booking(s, b.id, user_id=999) is None  # чужая запись
        assert await repo.cancel_booking(s, b.id, user_id=1) is not None
        assert await repo.busy_intervals(s, date(2026, 10, 20), TZ) == []
    await book(sm, 12, user_id=2)  # слот снова свободен


@pytest.mark.asyncio
async def test_user_upcoming_hides_past(sm):
    await book(sm, 11)
    await book(sm, 15)
    async with sm() as s:
        upcoming = await repo.user_upcoming(s, 1, now=local(12))
    assert [repo.as_utc(b.start_at) for b in upcoming] == [local(15).astimezone(UTC)]


def test_parse_day():
    from app.handlers.admin import parse_day

    today = date(2026, 12, 20)
    assert parse_day("25.12", today) == date(2026, 12, 25)
    assert parse_day("05.01", today) == date(2027, 1, 5)
    assert parse_day("2026-11-03", today) == date(2026, 11, 3)
    assert parse_day("ерунда", today) is None
