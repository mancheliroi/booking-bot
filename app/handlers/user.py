import logging
from datetime import UTC, date, datetime

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import repo, ui
from app.config import settings
from app.db import BookingStatus
from app.payments.registry import get_provider
from app.slots import free_slots, working_days

router = Router(name="user")
log = logging.getLogger(__name__)


class BookingForm(StatesGroup):
    master = State()
    service = State()
    day = State()
    time = State()
    phone = State()
    confirm = State()


def now_utc() -> datetime:
    return datetime.now(UTC)


async def _notify_company_admins(bot: Bot, session, company_id: int, text: str) -> None:
    for admin_id in await repo.company_admin_ids(session, company_id):
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            log.warning("Не удалось уведомить администратора %s", admin_id)


@router.message(CommandStart())
async def start(
    message: Message, command: CommandObject, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    await state.clear()
    payload = (command.args or "").strip()

    async with sessionmaker() as session:
        company = None
        if payload:
            company = await repo.get_company_by_slug(session, payload)
            if company is not None and company.is_active:
                await repo.set_client_company(session, message.from_user.id, company.id)
            else:
                company = None
        if company is None:
            company = await repo.get_client_company(session, message.from_user.id)
        if company is None or not company.is_active:
            await message.answer(ui.NOT_LINKED_TEXT)
            return
        text = await repo.get_text(session, company.id, "welcome")

    await message.answer(text, reply_markup=ui.main_menu())


@router.message(F.text == ui.BTN_CONTACTS)
async def contacts(message: Message, sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        company = await repo.get_client_company(session, message.from_user.id)
        if company is None:
            await message.answer(ui.NOT_LINKED_TEXT)
            return
        await message.answer(await repo.get_text(session, company.id, "contacts"))


@router.message(F.text == ui.BTN_BOOK)
async def start_booking(message: Message, state: FSMContext, sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        company = await repo.get_client_company(session, message.from_user.id)
        if company is None:
            await message.answer(ui.NOT_LINKED_TEXT)
            return
        masters = await repo.list_masters(session, company.id)

    if not masters:
        await message.answer("Сейчас запись недоступна. Загляните чуть позже 🙏")
        return

    await state.update_data(company_id=company.id)
    if len(masters) == 1:
        await state.update_data(master_id=masters[0].id)
        await _offer_services(message, state, sessionmaker, company.id)
        return

    await state.set_state(BookingForm.master)
    await message.answer("К кому вас записать?", reply_markup=ui.masters_kb(masters))


async def _offer_services(target, state: FSMContext, sessionmaker: async_sessionmaker, company_id: int) -> None:
    async with sessionmaker() as session:
        services = await repo.list_services(session, company_id)
    if not services:
        await target.answer("Сейчас запись недоступна. Загляните чуть позже 🙏")
        return
    await state.set_state(BookingForm.service)
    await target.answer("Выберите услугу:", reply_markup=ui.services_kb(services))


@router.callback_query(BookingForm.master, ui.MasterCb.filter())
async def choose_service(
    call: CallbackQuery, callback_data: ui.MasterCb, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    await state.update_data(master_id=callback_data.id)
    data = await state.get_data()
    async with sessionmaker() as session:
        services = await repo.list_services(session, data["company_id"])
    if not services:
        await call.message.edit_text("Сейчас запись недоступна. Загляните чуть позже 🙏")
        await call.answer()
        return
    await state.set_state(BookingForm.service)
    await call.message.edit_text("Выберите услугу:", reply_markup=ui.services_kb(services))
    await call.answer()


@router.callback_query(BookingForm.service, ui.ServiceCb.filter())
async def choose_day(
    call: CallbackQuery, callback_data: ui.ServiceCb, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    await state.update_data(service_id=callback_data.id)
    data = await state.get_data()
    await state.set_state(BookingForm.day)

    async with sessionmaker() as session:
        company = await repo.get_company(session, data["company_id"])
    today = now_utc().astimezone(company.tz).date()
    days = working_days(today, company.days_ahead, company.days_off)
    await call.message.edit_text("Выберите день:", reply_markup=ui.days_kb(days))
    await call.answer()


@router.callback_query(BookingForm.day, ui.DayCb.filter())
async def choose_time(
    call: CallbackQuery, callback_data: ui.DayCb, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    day = date.fromisoformat(callback_data.iso)
    data = await state.get_data()
    async with sessionmaker() as session:
        company = await repo.get_company(session, data["company_id"])
        service = await repo.get_service(session, data["service_id"])
        busy = await repo.busy_intervals(session, data["master_id"], day, company.tz)

    slots = free_slots(
        day,
        tz=company.tz,
        work_start_hour=company.work_start_hour,
        work_end_hour=company.work_end_hour,
        step_minutes=company.slot_minutes,
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
        company = await repo.get_company(session, data["company_id"])
        service = await repo.get_service(session, data["service_id"])
    start_at = datetime.fromtimestamp(data["start_ts"], UTC)

    prepay_note = ""
    if company.require_prepayment and service.price > 0:
        prepay_note = "\n\n💳 Для подтверждения потребуется оплата — ссылку пришлём сразу после."

    await message.answer("Проверьте данные 👇", reply_markup=ui.main_menu())
    await message.answer(
        f"<b>{service.name}</b>\n"
        f"🕒 {ui.fmt_dt(start_at, company.tz)} ({service.duration_min} мин)\n"
        f"💳 {ui.fmt_price(service.price)}\n"
        f"📱 {phone}"
        f"{prepay_note}",
        reply_markup=ui.confirm_kb(),
    )


@router.callback_query(BookingForm.confirm, ui.ConfirmCb.filter())
async def finish(
    call: CallbackQuery,
    callback_data: ui.ConfirmCb,
    state: FSMContext,
    sessionmaker: async_sessionmaker,
    bot: Bot,
    bot_username: str,
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
        company = await repo.get_company(session, data["company_id"])
        service = await repo.get_service(session, data["service_id"])

        need_payment = company.require_prepayment and service.price > 0
        provider = get_provider(company) if need_payment else None
        status = BookingStatus.awaiting_payment if (need_payment and provider is not None) else BookingStatus.active

        try:
            booking = await repo.create_booking(
                session,
                company_id=company.id,
                master_id=data["master_id"],
                user_id=call.from_user.id,
                client_name=call.from_user.full_name,
                phone=data["phone"],
                service=service,
                start_at=start_at,
                status=status,
            )
        except repo.SlotTakenError:
            await call.message.edit_text("Ой, это время только что заняли 😔 Выберите другое через «Записаться».")
            await call.answer()
            return

        when = ui.fmt_dt(start_at, company.tz)

        if status == BookingStatus.awaiting_payment:
            try:
                link = await provider.create_payment(
                    amount_rub=service.price,
                    description=f"{service.name} — {company.name}",
                    return_url=f"https://t.me/{bot_username}",
                )
            except Exception:
                log.exception("Не удалось создать платёж для записи %s", booking.id)
                await repo.cancel_booking(session, booking.id)
                await call.message.edit_text(
                    "Не получилось выставить счёт. Попробуйте ещё раз чуть позже или напишите нам в «Контакты»."
                )
                await call.answer()
                return

            await repo.set_booking_payment(session, booking.id, provider=company.payment_provider, payment_id=link.payment_id)
            await call.message.edit_text(
                f"<b>{service.name}</b>, {when}\n"
                f"Номер записи: {booking.id}\n\n"
                f"Время удержано на {settings.payment_hold_minutes} мин — оплатите, чтобы подтвердить запись.",
                reply_markup=ui.payment_kb(link.confirmation_url, booking.id),
            )
            await call.answer()
            await _notify_company_admins(
                bot, session, company.id, f"⏳ Новая запись №{booking.id} ждёт оплаты\n{service.name}, {when}"
            )
            return

        after_text = await repo.get_text(session, company.id, "booked")
        await call.message.edit_text(
            f"✅ Вы записаны!\n\n<b>{service.name}</b>, {when}\nНомер записи: {booking.id}\n\n{after_text}"
        )
        await call.answer()
        await _notify_company_admins(
            bot,
            session,
            company.id,
            f"🆕 Новая запись №{booking.id}\n{service.name}, {when}\n{booking.client_name}, {booking.phone}",
        )


@router.callback_query(ui.PayCheckCb.filter())
async def check_payment(
    call: CallbackQuery, callback_data: ui.PayCheckCb, sessionmaker: async_sessionmaker, bot: Bot
) -> None:
    async with sessionmaker() as session:
        booking = await repo.get_booking(session, callback_data.id)
        if booking is None or booking.user_id != call.from_user.id:
            await call.answer("Запись не найдена", show_alert=True)
            return
        if booking.status != BookingStatus.awaiting_payment:
            await call.answer("Эта запись уже обработана", show_alert=True)
            return

        company = await repo.get_company(session, booking.company_id)
        provider = get_provider(company)
        state = await provider.check_status(booking.payment_id) if provider else "pending"

        if state == "paid":
            booking = await repo.confirm_payment(session, booking.id)
            after_text = await repo.get_text(session, company.id, "booked")
            await call.message.edit_text(f"✅ Оплата получена! Вы записаны.\n\n{after_text}")
            await _notify_company_admins(bot, session, company.id, f"💳 Оплачена запись №{booking.id}")
        elif state == "canceled":
            await repo.cancel_booking(session, booking.id)
            await call.message.edit_text("Платёж отменён, время снова свободно.")
        else:
            await call.answer("Платёж ещё не поступил. Попробуйте через минуту.", show_alert=True)
            return
    await call.answer()


@router.message(F.text == ui.BTN_MY)
async def my_bookings(message: Message, sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        company = await repo.get_client_company(session, message.from_user.id)
        if company is None:
            await message.answer(ui.NOT_LINKED_TEXT)
            return
        bookings = await repo.user_upcoming(session, company.id, message.from_user.id, now_utc())
        tz = company.tz
    if not bookings:
        await message.answer("У вас нет предстоящих записей.")
        return
    lines = "\n".join(ui.booking_line(b, tz) for b in bookings)
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
        await _notify_company_admins(bot, session, booking.company_id, f"❌ Клиент отменил запись №{booking.id}")
