"""Мелкие чистые функции, нужные и супер-админу, и админу компании."""

from datetime import date, datetime, timedelta


def parse_day(text: str, today: date) -> date | None:
    """Принимает «25.10», «25.10.2026» или «2026-10-25»."""
    text = text.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        parsed = datetime.strptime(f"{text}.{today.year}", "%d.%m.%Y").date()
    except ValueError:
        return None
    # «05.01» в декабре — это уже следующий год
    return parsed.replace(year=today.year + 1) if parsed < today - timedelta(days=180) else parsed


def parse_int(text: str | None, *, min_value: int, max_value: int) -> int | None:
    digits = (text or "").replace(" ", "").strip()
    if not digits.isdigit():
        return None
    value = int(digits)
    return value if min_value <= value <= max_value else None


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(text: str) -> str:
    """Грубый транслит + чистка — используется только как подсказка, слаг всё равно подтверждает человек."""
    transliterated = "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())
    out = "".join(ch if ch.isalnum() else "_" for ch in transliterated)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")[:50] or "company"


def parse_schedule(text: str) -> dict | None:
    """Формат: «10-20 60 6» — часы работы, шаг слота в минутах, выходные дни через запятую (0=пн).

    Например «9-21 45 0,6» — с 9 до 21, слот 45 минут, выходные понедельник и воскресенье.
    """
    parts = text.strip().split()
    if len(parts) != 3:
        return None
    hours, step_raw, days_off_raw = parts
    if "-" not in hours:
        return None
    start_raw, end_raw = hours.split("-", 1)
    if not (start_raw.isdigit() and end_raw.isdigit() and step_raw.isdigit()):
        return None
    start, end, step = int(start_raw), int(end_raw), int(step_raw)
    if not (0 <= start < end <= 23) or not (5 <= step <= 12 * 60):
        return None
    days_off_raw = days_off_raw.strip()
    days_off: list[int] = []
    if days_off_raw not in ("", "-"):
        for chunk in days_off_raw.split(","):
            chunk = chunk.strip()
            if not chunk.isdigit() or not 0 <= int(chunk) <= 6:
                return None
            days_off.append(int(chunk))
    return {"work_start_hour": start, "work_end_hour": end, "slot_minutes": step, "days_off": sorted(set(days_off))}
