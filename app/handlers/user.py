import logging
from datetime import UTC, date, datetime

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import repo, ui
from app.config import settings
from app.slots import free_slots, working_days

router = Router(name="user")
log = logging.getLogger(__name__)


class BookingForm(StatesGroup):
    service = State()
    day = State()
    time = State()
    phone = State()
    confirm = State()


def now_utc() -> datetime:
    return datetime.now(UTC)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        f"Здравствуйте, {message.from_user.first_name}! 👋\n\n"
        "Я помогу записаться на услугу: выберите время — и всё готово.",
        reply_markup=ui.main_menu(),
    )


@router.message(F.text == ui.BTN_BOOK)
async def choose_service(message: Message, state: FSMContext, sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        services = await repo.list_services(session)
    await state.set_state(BookingForm.service)
    await message.answer("Выберите услугу:", reply_markup=ui.services_kb(services))


@router.callback_query(BookingForm.service, ui.ServiceCb.filter())
async def choose_day(call: CallbackQuery, callback_data: ui.ServiceCb, state: FSMContext) -> None:
    await state.update_data(service_id=callback_data.id)
    await state.set_state(BookingForm.day)
    today = now_utc().astimezone(settings.tz).date()
    days = working_days(today, settings.days_ahead, settings.days_off)
    await call.message.edit_text("Выберите день:", reply_markup=ui.days_kb(days))
    await call.answer()


@router.callback_query(BookingForm.day, ui.DayCb.filter())
async def choose_time(
    call: CallbackQuery, callback_data: ui.DayCb, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    day = date.fromisoformat(callback_data.iso)
    data = await state.get_data()
    async with sessionmaker() as session:
        service = await repo.get_service(session, data["service_id"])
        busy = await repo.busy_intervals(session, day, settings.tz)

    slots = free_slots(
        day,
        tz=settings.tz,
        work_start_hour=settings.work_start_hour,
        work_end_hour=settings.work_end_hour,
        step_minutes=settings.slot_minutes,
        duration_minutes=service.duration_min,
        busy=busy,
        now=now_utc(),
    )
    if not slots:
        await call.answer("На этот день свободного времени нет, выберите другой 🙏", show_alert=True)
        return

    await state.set_state(BookingForm.time)
    await call.message.edit_text(f"{ui.fmt_day(day)} — выберите время:", reply_markup=ui.times_kb(slots))
    await call.answer()


@router.callback_query(BookingForm.time, ui.TimeCb.filter())
async def ask_phone(call: CallbackQuery, callback_data: ui.TimeCb, state: FSMContext) -> None:
    await state.update_data(start_ts=callback_data.ts)
    await state.set_state(BookingForm.phone)
    await call.message.delete()
    await call.message.answer(
        "Оставьте номер телефона для связи — нажмите кнопку ниже или напишите его.",
        reply_markup=ui.phone_keyboard(),
    )
    await call.answer()


@router.message(BookingForm.phone)
async def confirm(message: Message, state: FSMContext, sessionmaker: async_sessionmaker) -> None:
    phone = message.contact.phone_number if message.contact else (message.text or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not 10 <= len(digits) <= 15:
        await message.answer("Похоже, номер указан неверно. Попробуйте ещё раз, например: +7 999 123-45-67")
        return

    await state.update_data(phone=phone)
    await state.set_state(BookingForm.confirm)
    data = await state.get_data()
    async with sessionmaker() as session:
        service = await repo.get_service(session, data["service_id"])
    start_at = datetime.fromtimestamp(data["start_ts"], UTC)

    await message.answer("Проверьте данные 👇", reply_markup=ui.main_menu())
    await message.answer(
        f"<b>{service.name}</b>\n"
        f"🕒 {ui.fmt_dt(start_at, settings.tz)} ({service.duration_min} мин)\n"
        f"💳 {ui.fmt_price(service.price)}\n"
        f"📱 {phone}",
        reply_markup=ui.confirm_kb(),
    )


@router.callback_query(BookingForm.confirm, ui.ConfirmCb.filter())
async def finish(
    call: CallbackQuery,
    callback_data: ui.ConfirmCb,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    bot: Bot,
) -> None:
    data = await state.get_data()
    await state.clear()
    if not callback_data.ok:
        await call.message.edit_text("Запись отменена. Можно начать заново в любой момент.")
        await call.answer()
        return

    start_at = datetime.fromtimestamp(data["start_ts"], UTC)
    if start_at <= now_utc():
        await call.message.edit_text("Это время уже прошло, выберите другое через «Записаться».")
        await call.answer()
        return

    async with sessionmaker() as session:
        service = await repo.get_service(session, data["service_id"])
        try:
            booking = await repo.create_booking(
                session,
                user_id=call.from_user.id,
                client_name=call.from_user.full_name,
                phone=data["phone"],
                service=service,
                start_at=start_at,
            )
        except repo.SlotTakenError:
            await call.message.edit_text("Ой, это время только что заняли 😔 Выберите другое через «Записаться».")
            await call.answer()
            return

    when = ui.fmt_dt(start_at, settings.tz)
    await call.message.edit_text(f"✅ Вы записаны!\n\n<b>{service.name}</b>, {when}\nНомер записи: {booking.id}")
    await call.answer()

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                f"🆕 Новая запись №{booking.id}\n{service.name}, {when}\n"
                f"{booking.client_name}, {booking.phone}",
            )
        except Exception:  # админ мог не запускать бота — не роняем запись
            log.warning("Не удалось уведомить администратора %s", admin_id)


@router.message(F.text == ui.BTN_MY)
async def my_bookings(message: Message, sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        bookings = await repo.user_upcoming(session, message.from_user.id, now_utc())
    if not bookings:
        await message.answer("У вас нет предстоящих записей.")
        return
    lines = "\n".join(ui.booking_line(b, settings.tz) for b in bookings)
    await message.answer(f"Ваши записи:\n{lines}", reply_markup=ui.my_bookings_kb(bookings))


@router.callback_query(ui.CancelCb.filter())
async def cancel(call: CallbackQuery, callback_data: ui.CancelCb, sessionmaker: async_sessionmaker, bot: Bot) -> None:
    async with sessionmaker() as session:
        booking = await repo.cancel_booking(session, callback_data.id, user_id=call.from_user.id)
    if booking is None:
        await call.answer("Запись не найдена или уже отменена", show_alert=True)
        return
    await call.message.edit_text(f"Запись №{booking.id} отменена.")
    await call.answer()
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, f"❌ Клиент отменил запись №{booking.id}")
        except Exception:
            log.warning("Не удалось уведомить администратора %s", admin_id)
