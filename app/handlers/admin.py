from datetime import UTC, date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import repo, ui
from app.config import settings

router = Router(name="admin")
router.message.filter(F.from_user.id.func(lambda uid: uid in settings.admin_ids))

HELP = (
    "<b>Панель администратора</b>\n\n"
    "/today — записи на сегодня\n"
    "/tomorrow — записи на завтра\n"
    "/day 25.10 — записи на дату\n"
    "/cancel 12 — отменить запись №12"
)


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


def today_local() -> date:
    return datetime.now(UTC).astimezone(settings.tz).date()


async def send_day(message: Message, day: date, sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        bookings = await repo.bookings_for_day(session, day, settings.tz)
    if not bookings:
        await message.answer(f"{ui.fmt_day(day)}: записей нет.")
        return
    lines = [
        f"{repo.as_utc(b.start_at).astimezone(settings.tz):%H:%M} — {b.service.name}\n"
        f"    №{b.id}, {b.client_name}, {b.phone}"
        for b in bookings
    ]
    await message.answer(f"<b>{ui.fmt_day(day)}</b> — записей: {len(bookings)}\n\n" + "\n".join(lines))


@router.message(Command("admin"))
async def admin_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("today"))
async def today(message: Message, sessionmaker: async_sessionmaker) -> None:
    await send_day(message, today_local(), sessionmaker)


@router.message(Command("tomorrow"))
async def tomorrow(message: Message, sessionmaker: async_sessionmaker) -> None:
    await send_day(message, today_local() + timedelta(days=1), sessionmaker)


@router.message(Command("day"))
async def by_day(message: Message, command: CommandObject, sessionmaker: async_sessionmaker) -> None:
    day = parse_day(command.args or "", today_local())
    if day is None:
        await message.answer("Укажите дату, например: /day 25.10")
        return
    await send_day(message, day, sessionmaker)


@router.message(Command("cancel"))
async def cancel(message: Message, command: CommandObject, sessionmaker: async_sessionmaker, bot: Bot) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Укажите номер записи, например: /cancel 12")
        return
    async with sessionmaker() as session:
        booking = await repo.cancel_booking(session, int(command.args))
    if booking is None:
        await message.answer("Запись не найдена или уже отменена.")
        return
    await message.answer(f"Запись №{booking.id} отменена.")
    try:
        await bot.send_message(
            booking.user_id,
            f"К сожалению, ваша запись №{booking.id} ({ui.fmt_dt(booking.start_at, settings.tz)}) отменена. "
            "Выберите другое время через «Записаться».",
        )
    except Exception:
        pass
