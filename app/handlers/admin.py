"""Панель администратора.

Всё управляется кнопками прямо в Telegram — администратору не нужен
программист, чтобы поменять цены, услуги или тексты бота.
"""

from datetime import UTC, date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import repo, ui
from app.config import settings
from app.db import EDITABLE_TEXTS, Service


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


router = Router(name="admin")
router.message.filter(F.from_user.id.func(is_admin))
router.callback_query.filter(F.from_user.id.func(is_admin))


# ---------- callback-данные ----------


class AdminCb(CallbackData, prefix="adm"):
    action: str  # menu | services | texts | today | tomorrow | add


class SvcCb(CallbackData, prefix="asvc"):
    id: int
    action: str  # open | name | price | duration_min | toggle


class TextCb(CallbackData, prefix="atxt"):
    key: str


class EditForm(StatesGroup):
    service_field = State()
    new_name = State()
    new_duration = State()
    new_price = State()
    text_value = State()


FIELD_PROMPTS = {
    "name": "Введите новое название услуги:",
    "price": "Введите новую цену в рублях (0 — бесплатно):",
    "duration_min": "Введите длительность в минутах (например, 60):",
}


# Команды (/start, /admin…) не должны попадать в поля ввода
NOT_COMMAND = F.text & ~F.text.startswith("/")


# ---------- клавиатуры ----------


def menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Записи на сегодня", callback_data=AdminCb(action="today"))
    kb.button(text="📅 На завтра", callback_data=AdminCb(action="tomorrow"))
    kb.button(text="💇 Услуги и цены", callback_data=AdminCb(action="services"))
    kb.button(text="✏️ Тексты бота", callback_data=AdminCb(action="texts"))
    kb.adjust(2, 1, 1)
    return kb.as_markup()


def services_kb(services: list[Service]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in services:
        mark = "" if s.active else "🙈 "
        kb.button(text=f"{mark}{s.name} · {ui.fmt_price(s.price)}", callback_data=SvcCb(id=s.id, action="open"))
    kb.button(text="➕ Добавить услугу", callback_data=AdminCb(action="add"))
    kb.button(text="⬅️ Меню", callback_data=AdminCb(action="menu"))
    kb.adjust(1)
    return kb.as_markup()


def service_card_kb(s: Service) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Название", callback_data=SvcCb(id=s.id, action="name"))
    kb.button(text="💳 Цена", callback_data=SvcCb(id=s.id, action="price"))
    kb.button(text="🕒 Длительность", callback_data=SvcCb(id=s.id, action="duration_min"))
    kb.button(
        text="🙈 Скрыть от клиентов" if s.active else "👁 Показать клиентам",
        callback_data=SvcCb(id=s.id, action="toggle"),
    )
    kb.button(text="⬅️ К услугам", callback_data=AdminCb(action="services"))
    kb.adjust(3, 1, 1)
    return kb.as_markup()


def texts_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, (title, _) in EDITABLE_TEXTS.items():
        kb.button(text=title, callback_data=TextCb(key=key))
    kb.button(text="⬅️ Меню", callback_data=AdminCb(action="menu"))
    kb.adjust(1)
    return kb.as_markup()


def service_card(s: Service) -> str:
    status = "видна клиентам" if s.active else "скрыта от клиентов"
    return (
        f"<b>{s.name}</b>\n"
        f"Цена: {ui.fmt_price(s.price)}\n"
        f"Длительность: {s.duration_min} мин\n"
        f"Статус: {status}"
    )


def parse_int(text: str | None, *, min_value: int, max_value: int) -> int | None:
    digits = (text or "").replace(" ", "").strip()
    if not digits.isdigit():
        return None
    value = int(digits)
    return value if min_value <= value <= max_value else None


# ---------- расписание ----------


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


async def day_report(day: date, sessionmaker: async_sessionmaker) -> str:
    async with sessionmaker() as session:
        bookings = await repo.bookings_for_day(session, day, settings.tz)
    if not bookings:
        return f"{ui.fmt_day(day)}: записей нет."
    lines = [
        f"{repo.as_utc(b.start_at).astimezone(settings.tz):%H:%M} — {b.service.name}\n"
        f"    №{b.id}, {b.client_name}, {b.phone}"
        for b in bookings
    ]
    return f"<b>{ui.fmt_day(day)}</b> — записей: {len(bookings)}\n\n" + "\n".join(lines)


# ---------- команды ----------

HELP = (
    "<b>Панель администратора</b>\n\n"
    "Всё можно менять кнопками ниже.\n\n"
    "Команды:\n"
    "/day 25.10 — записи на дату\n"
    "/cancel 12 — отменить запись №12"
)


@router.message(Command("admin"))
async def admin_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HELP, reply_markup=menu_kb())


@router.message(Command("today"))
async def today_cmd(message: Message, sessionmaker: async_sessionmaker) -> None:
    await message.answer(await day_report(today_local(), sessionmaker))


@router.message(Command("tomorrow"))
async def tomorrow_cmd(message: Message, sessionmaker: async_sessionmaker) -> None:
    await message.answer(await day_report(today_local() + timedelta(days=1), sessionmaker))


@router.message(Command("day"))
async def by_day(message: Message, command: CommandObject, sessionmaker: async_sessionmaker) -> None:
    day = parse_day(command.args or "", today_local())
    if day is None:
        await message.answer("Укажите дату, например: /day 25.10")
        return
    await message.answer(await day_report(day, sessionmaker))


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


# ---------- кнопки меню ----------


@router.callback_query(AdminCb.filter())
async def menu_action(
    call: CallbackQuery, callback_data: AdminCb, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    await state.clear()
    action = callback_data.action
    if action == "menu":
        await call.message.edit_text(HELP, reply_markup=menu_kb())
    elif action in ("today", "tomorrow"):
        day = today_local() + timedelta(days=1 if action == "tomorrow" else 0)
        await call.message.answer(await day_report(day, sessionmaker))
    elif action == "services":
        async with sessionmaker() as session:
            services = await repo.list_services(session, include_hidden=True)
        await call.message.edit_text(
            "Услуги. Нажмите на услугу, чтобы изменить её.\n🙈 — скрыта от клиентов.",
            reply_markup=services_kb(services),
        )
    elif action == "texts":
        await call.message.edit_text("Какой текст изменить?", reply_markup=texts_kb())
    elif action == "add":
        await state.set_state(EditForm.new_name)
        await call.message.answer("Новая услуга. Введите название:")
    await call.answer()


# ---------- редактирование услуг ----------


@router.callback_query(SvcCb.filter())
async def service_action(
    call: CallbackQuery, callback_data: SvcCb, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    async with sessionmaker() as session:
        service = await repo.get_service(session, callback_data.id)
        if service is None:
            await call.answer("Услуга не найдена", show_alert=True)
            return
        if callback_data.action == "toggle":
            service = await repo.update_service(session, service.id, active=not service.active)
            await call.answer("Готово")
            await call.message.edit_text(service_card(service), reply_markup=service_card_kb(service))
            return

    if callback_data.action == "open":
        await call.message.edit_text(service_card(service), reply_markup=service_card_kb(service))
    else:
        await state.set_state(EditForm.service_field)
        await state.update_data(service_id=service.id, field=callback_data.action)
        await call.message.answer(f"{service.name}. {FIELD_PROMPTS[callback_data.action]}")
    await call.answer()


@router.message(EditForm.service_field, NOT_COMMAND)
async def save_service_field(message: Message, state: FSMContext, sessionmaker: async_sessionmaker) -> None:
    data = await state.get_data()
    field = data["field"]
    if field == "name":
        value = (message.text or "").strip()
        if not 1 <= len(value) <= 100:
            await message.answer("Название должно быть от 1 до 100 символов.")
            return
    elif field == "price":
        value = parse_int(message.text, min_value=0, max_value=10_000_000)
        if value is None:
            await message.answer("Введите цену числом, например: 1500")
            return
    else:
        value = parse_int(message.text, min_value=5, max_value=12 * 60)
        if value is None:
            await message.answer("Введите длительность в минутах от 5 до 720, например: 60")
            return

    async with sessionmaker() as session:
        service = await repo.update_service(session, data["service_id"], **{field: value})
    await state.clear()
    await message.answer("✅ Сохранено\n\n" + service_card(service), reply_markup=service_card_kb(service))


@router.message(EditForm.new_name, NOT_COMMAND)
async def add_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 100:
        await message.answer("Название должно быть от 1 до 100 символов.")
        return
    await state.update_data(name=name)
    await state.set_state(EditForm.new_duration)
    await message.answer("Длительность в минутах (например, 60):")


@router.message(EditForm.new_duration, NOT_COMMAND)
async def add_duration(message: Message, state: FSMContext) -> None:
    duration = parse_int(message.text, min_value=5, max_value=12 * 60)
    if duration is None:
        await message.answer("Введите длительность в минутах от 5 до 720, например: 60")
        return
    await state.update_data(duration=duration)
    await state.set_state(EditForm.new_price)
    await message.answer("Цена в рублях (0 — бесплатно):")


@router.message(EditForm.new_price, NOT_COMMAND)
async def add_price(message: Message, state: FSMContext, sessionmaker: async_sessionmaker) -> None:
    price = parse_int(message.text, min_value=0, max_value=10_000_000)
    if price is None:
        await message.answer("Введите цену числом, например: 1500")
        return
    data = await state.get_data()
    async with sessionmaker() as session:
        service = await repo.add_service(session, name=data["name"], duration_min=data["duration"], price=price)
    await state.clear()
    await message.answer(
        "✅ Услуга добавлена и уже видна клиентам\n\n" + service_card(service),
        reply_markup=service_card_kb(service),
    )


# ---------- редактирование текстов ----------


@router.callback_query(TextCb.filter())
async def text_action(
    call: CallbackQuery, callback_data: TextCb, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    async with sessionmaker() as session:
        current = await repo.get_text(session, callback_data.key)
    title = EDITABLE_TEXTS[callback_data.key][0]
    await state.set_state(EditForm.text_value)
    await state.update_data(key=callback_data.key)
    await call.message.answer(f"<b>{title}</b> сейчас:\n\n{current}\n\nОтправьте новый текст одним сообщением.")
    await call.answer()


@router.message(EditForm.text_value, NOT_COMMAND)
async def save_text(message: Message, state: FSMContext, sessionmaker: async_sessionmaker) -> None:
    value = (message.html_text or "").strip()
    if not 1 <= len(value) <= 2000:
        await message.answer("Текст должен быть от 1 до 2000 символов.")
        return
    data = await state.get_data()
    async with sessionmaker() as session:
        await repo.set_text(session, data["key"], value)
    await state.clear()
    await message.answer("✅ Текст обновлён — клиенты уже видят новую версию.", reply_markup=texts_kb())
