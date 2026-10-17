"""Панель администратора компании.

Всё управляется кнопками прямо в Telegram — владельцу компании не нужен
программист, чтобы поменять цены, услуги, мастеров, расписание или
подключить приём онлайн-оплаты.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import repo, ui
from app.admin_utils import parse_day, parse_int, parse_schedule
from app.db import EDITABLE_TEXTS, Company, Master, Service

log = logging.getLogger(__name__)


class IsCompanyAdmin(BaseFilter):
    async def __call__(self, event, sessionmaker: async_sessionmaker) -> bool:
        async with sessionmaker() as session:
            companies = await repo.companies_for_admin(session, event.from_user.id)
        return bool(companies)


router = Router(name="company_admin")
router.message.filter(IsCompanyAdmin())
router.callback_query.filter(IsCompanyAdmin())


# ---------- callback-данные ----------


class AdminCb(CallbackData, prefix="adm"):
    action: str  # menu | services | texts | masters | schedule | payment | stats | broadcast | add_service | add_master


class SvcCb(CallbackData, prefix="asvc"):
    id: int
    action: str  # open | name | price | duration_min | toggle


class MstrCb(CallbackData, prefix="amstr"):
    id: int
    action: str  # open | rename | toggle


class TextCb(CallbackData, prefix="atxt"):
    key: str


class PaymentCb(CallbackData, prefix="apay"):
    action: str  # yookassa | stripe | toggle_prepay


class Form(StatesGroup):
    """Один state на все текстовые вводы — какой именно шаг, определяет data['step']."""

    input = State()


NOT_COMMAND = F.text & ~F.text.startswith("/")

FIELD_PROMPTS = {
    "name": "Введите новое название услуги:",
    "price": "Введите новую цену в рублях (0 — бесплатно):",
    "duration_min": "Введите длительность в минутах (например, 60):",
}


# ---------- клавиатуры ----------


def menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Сегодня", callback_data=AdminCb(action="today"))
    kb.button(text="📅 Завтра", callback_data=AdminCb(action="tomorrow"))
    kb.button(text="💇 Услуги и цены", callback_data=AdminCb(action="services"))
    kb.button(text="👥 Сотрудники", callback_data=AdminCb(action="masters"))
    kb.button(text="✏️ Тексты бота", callback_data=AdminCb(action="texts"))
    kb.button(text="⏰ Расписание", callback_data=AdminCb(action="schedule"))
    kb.button(text="💳 Оплата", callback_data=AdminCb(action="payment"))
    kb.button(text="📊 Статистика", callback_data=AdminCb(action="stats"))
    kb.button(text="📣 Рассылка", callback_data=AdminCb(action="broadcast"))
    kb.adjust(2, 2, 2, 1, 1)
    return kb.as_markup()


def services_kb(services: list[Service]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in services:
        mark = "" if s.active else "🙈 "
        kb.button(text=f"{mark}{s.name} · {ui.fmt_price(s.price)}", callback_data=SvcCb(id=s.id, action="open"))
    kb.button(text="➕ Добавить услугу", callback_data=AdminCb(action="add_service"))
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


def masters_kb(masters: list[Master]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for m in masters:
        mark = "" if m.active else "🙈 "
        kb.button(text=f"{mark}{m.name}", callback_data=MstrCb(id=m.id, action="open"))
    kb.button(text="➕ Добавить сотрудника", callback_data=AdminCb(action="add_master"))
    kb.button(text="⬅️ Меню", callback_data=AdminCb(action="menu"))
    kb.adjust(1)
    return kb.as_markup()


def master_card_kb(m: Master) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Переименовать", callback_data=MstrCb(id=m.id, action="rename"))
    kb.button(
        text="🙈 Скрыть" if m.active else "👁 Показать",
        callback_data=MstrCb(id=m.id, action="toggle"),
    )
    kb.button(text="⬅️ К сотрудникам", callback_data=AdminCb(action="masters"))
    kb.adjust(2, 1)
    return kb.as_markup()


def texts_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, (title, _) in EDITABLE_TEXTS.items():
        kb.button(text=title, callback_data=TextCb(key=key))
    kb.button(text="⬅️ Меню", callback_data=AdminCb(action="menu"))
    kb.adjust(1)
    return kb.as_markup()


def payment_kb(company: Company) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    prepay = "✅ Предоплата включена" if company.require_prepayment else "⬜ Предоплата выключена"
    kb.button(text=prepay, callback_data=PaymentCb(action="toggle_prepay"))
    kb.button(text="🏦 Подключить ЮKassa", callback_data=PaymentCb(action="yookassa"))
    kb.button(text="🏦 Подключить Stripe", callback_data=PaymentCb(action="stripe"))
    kb.button(text="⬅️ Меню", callback_data=AdminCb(action="menu"))
    kb.adjust(1)
    return kb.as_markup()


def service_card(s: Service) -> str:
    status = "видна клиентам" if s.active else "скрыта от клиентов"
    return (
        f"<b>{s.name}</b>\nЦена: {ui.fmt_price(s.price)}\nДлительность: {s.duration_min} мин\nСтатус: {status}"
    )


def master_card(m: Master) -> str:
    status = "принимает записи" if m.active else "скрыт от клиентов"
    return f"<b>{m.name}</b>\nСтатус: {status}"


def payment_status(company: Company) -> str:
    if not company.payment_provider:
        return "Провайдер не подключён — записи без предоплаты."
    label = {"yookassa": "ЮKassa", "stripe": "Stripe"}.get(company.payment_provider, company.payment_provider)
    return f"Провайдер: {label}\nПредоплата: {'включена' if company.require_prepayment else 'выключена'}"


# ---------- расписание отчётов ----------


async def day_report(company_id: int, day: date, tz, sessionmaker: async_sessionmaker) -> str:
    async with sessionmaker() as session:
        bookings = await repo.bookings_for_day(session, company_id, day, tz)
    if not bookings:
        return f"{ui.fmt_day(day)}: записей нет."
    lines = [
        f"{repo.as_utc(b.start_at).astimezone(tz):%H:%M} — {b.service.name} ({b.master.name})\n"
        f"    №{b.id}, {b.client_name}, {b.phone}"
        for b in bookings
    ]
    return f"<b>{ui.fmt_day(day)}</b> — записей: {len(bookings)}\n\n" + "\n".join(lines)


# ---------- вход в панель ----------

HELP = "<b>Панель администратора</b>\n\nВсё можно менять кнопками ниже.\n\nКоманда: /day 25.10 — записи на дату"


async def _enter_company(message: Message, state: FSMContext, company: Company) -> None:
    await state.clear()
    await state.update_data(company_id=company.id)
    await message.answer(f"<b>{company.name}</b>\n\n{HELP}", reply_markup=menu_kb())


@router.message(Command("admin"))
async def admin_entry(message: Message, state: FSMContext, sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        companies = await repo.companies_for_admin(session, message.from_user.id)
    if len(companies) == 1:
        await _enter_company(message, state, companies[0])
        return
    await state.clear()
    await message.answer("Вы администратор нескольких компаний. Выберите, с какой работать:", reply_markup=ui.companies_kb(companies))


@router.callback_query(ui.CompanyCb.filter())
async def pick_company(call: CallbackQuery, callback_data: ui.CompanyCb, state: FSMContext, sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        companies = await repo.companies_for_admin(session, call.from_user.id)
        company = next((c for c in companies if c.id == callback_data.id), None)
    if company is None:
        await call.answer("Нет доступа к этой компании", show_alert=True)
        return
    await state.clear()
    await state.update_data(company_id=company.id)
    await call.message.edit_text(f"<b>{company.name}</b>\n\n{HELP}", reply_markup=menu_kb())
    await call.answer()


async def _company_id(state: FSMContext) -> int | None:
    data = await state.get_data()
    return data.get("company_id")


@router.message(Command("day"))
async def by_day(message: Message, command: CommandObject, state: FSMContext, sessionmaker: async_sessionmaker) -> None:
    company_id = await _company_id(state)
    if company_id is None:
        await message.answer("Сначала откройте /admin")
        return
    async with sessionmaker() as session:
        company = await repo.get_company(session, company_id)
    day = parse_day(command.args or "", datetime.now(UTC).astimezone(company.tz).date())
    if day is None:
        await message.answer("Укажите дату, например: /day 25.10")
        return
    await message.answer(await day_report(company_id, day, company.tz, sessionmaker))


# ---------- главное меню ----------


@router.callback_query(AdminCb.filter(F.action != "broadcast_send"))
async def menu_action(
    call: CallbackQuery, callback_data: AdminCb, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    company_id = await _company_id(state)
    if company_id is None:
        await call.answer("Сессия истекла, откройте /admin заново", show_alert=True)
        return
    async with sessionmaker() as session:
        company = await repo.get_company(session, company_id)
    action = callback_data.action

    if action == "menu":
        await state.set_state(None)
        await call.message.edit_text(f"<b>{company.name}</b>\n\n{HELP}", reply_markup=menu_kb())

    elif action in ("today", "tomorrow"):
        today = datetime.now(UTC).astimezone(company.tz).date()
        day = today + timedelta(days=1 if action == "tomorrow" else 0)
        await call.message.answer(await day_report(company_id, day, company.tz, sessionmaker))

    elif action == "services":
        async with sessionmaker() as session:
            services = await repo.list_services(session, company_id, include_hidden=True)
        await call.message.edit_text(
            "Услуги. Нажмите на услугу, чтобы изменить её.\n🙈 — скрыта от клиентов.",
            reply_markup=services_kb(services),
        )

    elif action == "add_service":
        await state.set_state(Form.input)
        await state.update_data(step="new_service:name")
        await call.message.answer("Новая услуга. Введите название:")

    elif action == "masters":
        async with sessionmaker() as session:
            masters = await repo.list_masters(session, company_id, include_hidden=True)
        await call.message.edit_text(
            "Сотрудники. У каждого своё расписание записей.\n🙈 — скрыт от клиентов.",
            reply_markup=masters_kb(masters),
        )

    elif action == "add_master":
        await state.set_state(Form.input)
        await state.update_data(step="new_master")
        await call.message.answer("Введите имя нового сотрудника:")

    elif action == "texts":
        await call.message.edit_text("Какой текст изменить?", reply_markup=texts_kb())

    elif action == "schedule":
        off = ",".join(str(d) for d in company.days_off) or "-"
        await call.message.answer(
            "Текущее расписание: "
            f"{company.work_start_hour}-{company.work_end_hour}, шаг {company.slot_minutes} мин, выходные {off}\n\n"
            "Чтобы изменить, отправьте в формате «часы-часы шаг выходные», например:\n"
            "<code>9-21 45 0,6</code> — с 9 до 21, слот 45 минут, выходные пн и вс",
        )
        await state.set_state(Form.input)
        await state.update_data(step="schedule")

    elif action == "payment":
        await call.message.edit_text(payment_status(company), reply_markup=payment_kb(company))

    elif action == "stats":
        async with sessionmaker() as session:
            stats = await repo.company_stats(session, company_id)
        await call.message.answer(ui.fmt_stats(stats, company.name))

    elif action == "broadcast":
        await state.set_state(Form.input)
        await state.update_data(step="broadcast")
        await call.message.answer("Отправьте текст рассылки одним сообщением — его получат все, кто писал боту.")

    await call.answer()


# ---------- услуги ----------


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
        await state.set_state(Form.input)
        await state.update_data(step=f"svc:{callback_data.action}", service_id=service.id)
        await call.message.answer(f"{service.name}. {FIELD_PROMPTS[callback_data.action]}")
    await call.answer()


# ---------- сотрудники ----------


@router.callback_query(MstrCb.filter())
async def master_action(
    call: CallbackQuery, callback_data: MstrCb, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    async with sessionmaker() as session:
        master = await repo.get_master(session, callback_data.id)
        if master is None:
            await call.answer("Сотрудник не найден", show_alert=True)
            return
        if callback_data.action == "toggle":
            master = await repo.update_master(session, master.id, active=not master.active)
            await call.answer("Готово")
            await call.message.edit_text(master_card(master), reply_markup=master_card_kb(master))
            return

    if callback_data.action == "open":
        await call.message.edit_text(master_card(master), reply_markup=master_card_kb(master))
    else:
        await state.set_state(Form.input)
        await state.update_data(step="master:rename", master_id=master.id)
        await call.message.answer(f"{master.name}. Введите новое имя:")
    await call.answer()


# ---------- тексты ----------


@router.callback_query(TextCb.filter())
async def text_action(
    call: CallbackQuery, callback_data: TextCb, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    company_id = await _company_id(state)
    async with sessionmaker() as session:
        current = await repo.get_text(session, company_id, callback_data.key)
    title = EDITABLE_TEXTS[callback_data.key][0]
    await state.set_state(Form.input)
    await state.update_data(step=f"text:{callback_data.key}")
    await call.message.answer(f"<b>{title}</b> сейчас:\n\n{current}\n\nОтправьте новый текст одним сообщением.")
    await call.answer()


# ---------- оплата ----------


@router.callback_query(PaymentCb.filter())
async def payment_action(
    call: CallbackQuery, callback_data: PaymentCb, state: FSMContext, sessionmaker: async_sessionmaker
) -> None:
    company_id = await _company_id(state)
    if callback_data.action == "toggle_prepay":
        async with sessionmaker() as session:
            company = await repo.toggle_prepayment(session, company_id)
        await call.message.edit_text(payment_status(company), reply_markup=payment_kb(company))
        await call.answer("Готово")
        return

    provider = callback_data.action  # "yookassa" | "stripe"
    await state.set_state(Form.input)
    if provider == "yookassa":
        await state.update_data(step="payment:shop_id", provider=provider)
        await call.message.answer("ЮKassa: отправьте shopId из личного кабинета.")
    else:
        await state.update_data(step="payment:secret", provider=provider)
        await call.message.answer("Stripe: отправьте Secret key (sk_live_… или sk_test_…).")
    await call.answer()


# ---------- общий обработчик текстовых шагов ----------


@router.message(Form.input, NOT_COMMAND)
async def handle_input(message: Message, state: FSMContext, sessionmaker: async_sessionmaker, bot: Bot) -> None:
    data = await state.get_data()
    step = data["step"]
    company_id = data["company_id"]
    value = (message.text or "").strip()

    if step == "svc:name":
        if not 1 <= len(value) <= 100:
            await message.answer("Название должно быть от 1 до 100 символов.")
            return
        await _save_service_field(message, state, sessionmaker, data, "name", value)

    elif step == "svc:price":
        price = parse_int(value, min_value=0, max_value=10_000_000)
        if price is None:
            await message.answer("Введите цену числом, например: 1500")
            return
        await _save_service_field(message, state, sessionmaker, data, "price", price)

    elif step == "svc:duration_min":
        duration = parse_int(value, min_value=5, max_value=12 * 60)
        if duration is None:
            await message.answer("Введите длительность в минутах от 5 до 720, например: 60")
            return
        await _save_service_field(message, state, sessionmaker, data, "duration_min", duration)

    elif step == "new_service:name":
        if not 1 <= len(value) <= 100:
            await message.answer("Название должно быть от 1 до 100 символов.")
            return
        await state.update_data(step="new_service:duration", new_name=value)
        await message.answer("Длительность в минутах (например, 60):")

    elif step == "new_service:duration":
        duration = parse_int(value, min_value=5, max_value=12 * 60)
        if duration is None:
            await message.answer("Введите длительность в минутах от 5 до 720, например: 60")
            return
        await state.update_data(step="new_service:price", new_duration=duration)
        await message.answer("Цена в рублях (0 — бесплатно):")

    elif step == "new_service:price":
        price = parse_int(value, min_value=0, max_value=10_000_000)
        if price is None:
            await message.answer("Введите цену числом, например: 1500")
            return
        async with sessionmaker() as session:
            service = await repo.add_service(
                session, company_id=company_id, name=data["new_name"], duration_min=data["new_duration"], price=price
            )
        await state.set_state(None)
        await message.answer(
            "✅ Услуга добавлена и уже видна клиентам\n\n" + service_card(service),
            reply_markup=service_card_kb(service),
        )

    elif step == "new_master":
        if not 1 <= len(value) <= 100:
            await message.answer("Имя должно быть от 1 до 100 символов.")
            return
        async with sessionmaker() as session:
            master = await repo.add_master(session, company_id, value)
        await state.set_state(None)
        await message.answer("✅ Сотрудник добавлен\n\n" + master_card(master), reply_markup=master_card_kb(master))

    elif step == "master:rename":
        if not 1 <= len(value) <= 100:
            await message.answer("Имя должно быть от 1 до 100 символов.")
            return
        async with sessionmaker() as session:
            master = await repo.update_master(session, data["master_id"], name=value)
        await state.set_state(None)
        await message.answer("✅ Сохранено\n\n" + master_card(master), reply_markup=master_card_kb(master))

    elif step.startswith("text:"):
        key = step.split(":", 1)[1]
        text_value = (message.html_text or "").strip()
        if not 1 <= len(text_value) <= 2000:
            await message.answer("Текст должен быть от 1 до 2000 символов.")
            return
        async with sessionmaker() as session:
            await repo.set_text(session, company_id, key, text_value)
        await state.set_state(None)
        await message.answer("✅ Текст обновлён — клиенты уже видят новую версию.", reply_markup=texts_kb())

    elif step == "schedule":
        parsed = parse_schedule(value)
        if parsed is None:
            await message.answer("Не разобрал формат. Пример: <code>9-21 45 0,6</code>")
            return
        async with sessionmaker() as session:
            await repo.set_company_schedule(session, company_id, **parsed)
        await state.set_state(None)
        await message.answer("✅ Расписание обновлено.", reply_markup=menu_kb())

    elif step == "payment:shop_id":
        async with sessionmaker() as session:
            await repo.set_company_payment(session, company_id, provider=data["provider"], shop_id=value)
        await state.update_data(step="payment:secret")
        await message.answer("Теперь отправьте секретный ключ ЮKassa.")

    elif step == "payment:secret":
        async with sessionmaker() as session:
            await repo.set_company_payment(session, company_id, provider=data.get("provider"), secret_key=value)
            company = await repo.get_company(session, company_id)
        await state.set_state(None)
        # удаляем сообщение с ключом из чата — секрет не должен висеть в истории
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(
            "✅ Платёжный провайдер подключён.\n\n" + payment_status(company), reply_markup=payment_kb(company)
        )

    elif step == "broadcast":
        await state.update_data(step="broadcast_confirm", broadcast_text=message.html_text or value)
        async with sessionmaker() as session:
            targets = await repo.broadcast_targets(session, company_id)
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Отправить", callback_data=AdminCb(action="broadcast_send"))
        kb.button(text="✖️ Отмена", callback_data=AdminCb(action="menu"))
        await message.answer(
            f"Получат рассылку: {len(targets)} чел.\n\n«{message.html_text or value}»\n\nОтправляем?",
            reply_markup=kb.as_markup(),
        )


@router.callback_query(AdminCb.filter(F.action == "broadcast_send"))
async def broadcast_send(call: CallbackQuery, state: FSMContext, sessionmaker: async_sessionmaker, bot: Bot) -> None:
    data = await state.get_data()
    company_id = data.get("company_id")
    text = data.get("broadcast_text")
    if company_id is None or not text:
        await call.answer("Нечего отправлять", show_alert=True)
        return

    async with sessionmaker() as session:
        targets = await repo.broadcast_targets(session, company_id)
    await state.set_state(None)
    await call.message.edit_text(f"📣 Рассылка запущена, получателей: {len(targets)}")
    await call.answer()

    sent = failed = 0
    for telegram_id in targets:
        try:
            await bot.send_message(telegram_id, text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # не упираемся в лимиты Telegram на отправку сообщений

    await call.message.answer(f"✅ Рассылка завершена: доставлено {sent}, не удалось {failed}.")


async def _save_service_field(message: Message, state: FSMContext, sessionmaker: async_sessionmaker, data: dict, field: str, value) -> None:
    async with sessionmaker() as session:
        service = await repo.update_service(session, data["service_id"], **{field: value})
    await state.set_state(None)
    await message.answer("✅ Сохранено\n\n" + service_card(service), reply_markup=service_card_kb(service))
