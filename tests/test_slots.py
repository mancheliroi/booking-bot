from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.slots import free_slots, overlaps, working_days

TZ = ZoneInfo("Europe/Moscow")
DAY = date(2026, 10, 20)  # вторник


def at(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def slots(**kw):
    params = dict(
        tz=TZ,
        work_start_hour=10,
        work_end_hour=14,
        step_minutes=60,
        duration_minutes=60,
        busy=[],
        now=at(0),
    )
    params.update(kw)
    return free_slots(DAY, **params)


def test_empty_day_gives_all_slots():
    assert [s.hour for s in slots()] == [10, 11, 12, 13]


def test_busy_slot_is_excluded():
    assert [s.hour for s in slots(busy=[(at(11), at(12))])] == [10, 12, 13]


def test_long_service_must_fit_before_closing():
    # 2-часовая услуга не может начаться в 13:00 при закрытии в 14:00
    assert [s.hour for s in slots(duration_minutes=120)] == [10, 11, 12]


def test_long_service_cannot_overlap_booking():
    # Запись на 12:00 блокирует 2-часовые слоты в 11:00 и 12:00
    result = slots(duration_minutes=120, busy=[(at(12), at(13))])
    assert [s.hour for s in result] == [10]


def test_past_slots_are_hidden():
    assert [s.hour for s in slots(now=at(11, 30))] == [12, 13]


def test_half_hour_step():
    result = slots(step_minutes=30, duration_minutes=30, work_end_hour=11)
    assert [(s.hour, s.minute) for s in result] == [(10, 0), (10, 30)]


def test_overlaps_touching_intervals_do_not_overlap():
    assert not overlaps((at(10), at(11)), (at(11), at(12)))
    assert overlaps((at(10), at(11, 30)), (at(11), at(12)))


def test_working_days_skip_days_off():
    days = working_days(date(2026, 10, 17), 3, days_off=[6])  # суббота
    assert days == [date(2026, 10, 17), date(2026, 10, 19), date(2026, 10, 20)]
    assert all(d.weekday() != 6 for d in days)
    assert days[-1] - days[0] == timedelta(days=3)
