"""Логика расчёта свободного времени. Чистые функции без БД — легко тестировать."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

Interval = tuple[datetime, datetime]


def overlaps(a: Interval, b: Interval) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def working_days(today: date, days_ahead: int, days_off: list[int]) -> list[date]:
    """Ближайшие рабочие дни, начиная с сегодняшнего."""
    days = []
    current = today
    while len(days) < days_ahead:
        if current.weekday() not in days_off:
            days.append(current)
        current += timedelta(days=1)
    return days


def free_slots(
    day: date,
    *,
    tz: ZoneInfo,
    work_start_hour: int,
    work_end_hour: int,
    step_minutes: int,
    duration_minutes: int,
    busy: list[Interval],
    now: datetime,
) -> list[datetime]:
    """Возвращает время начала свободных слотов (в часовом поясе tz).

    Слот подходит, если услуга целиком помещается в рабочий день,
    не пересекается с занятыми интервалами и начинается позже текущего момента.
    """
    day_start = datetime.combine(day, time(work_start_hour), tzinfo=tz)
    day_end = datetime.combine(day, time(work_end_hour), tzinfo=tz)
    step = timedelta(minutes=step_minutes)
    duration = timedelta(minutes=duration_minutes)

    result = []
    start = day_start
    while start + duration <= day_end:
        candidate = (start, start + duration)
        if start > now and not any(overlaps(candidate, b) for b in busy):
            result.append(start)
        start += step
    return result
