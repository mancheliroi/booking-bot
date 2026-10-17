"""Клавиатуры и форматирование сообщений."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db import Booking, Company, CompanyAdmin, Master, Service
from app.repo import as_utc

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

BTN_BOOK = "📅 Записаться"
BTN_MY = "📋 Мои записи"
BTN_CONTACTS = "ℹ️ Контакты"

NOT_LINKED_TEXT = (
    "Этот бот обслуживает разные компании по отдельной ссылке.\n"
    "Если вам прислали ссылку-приглашение — перейдите по ней, и я вас запомню."
)


class MasterCb(CallbackData, prefix="mstr"):
    id: int


class ServiceCb(CallbackData, prefix="svc"):
    id: int


class DayCb(CallbackData, prefix="day"):
    iso: str


class TimeCb(CallbackData, prefix="tm"):
    ts: int  # unix timestamp начала слота


class ConfirmCb(CallbackData, prefix="cf"):
    ok: bool


class CancelCb(CallbackData, prefix="cn"):
    id: int


class PayCheckCb(CallbackData, prefix="paych"):
    id: int


class CompanyCb(CallbackData, prefix="co"):
    id: int


def fmt_price(price: int) -> str:
    return "бесплатно" if price == 0 else f"{price:,} ₽".replace(",", " ")


def fmt_day(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]}, {day:%d.%m}"


def fmt_dt(dt: datetime, tz: ZoneInfo) -> str:
    local = as_utc(dt).astimezone(tz)
    return f"{fmt_day(local.date())} в {local:%H:%M}"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK), KeyboardButton(text=BTN_MY)],
            [KeyboardButton(text=BTN_CONTACTS)],
        ],
        resize_keyboard=True,
    )


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def masters_kb(masters: list[Master]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for m in masters:
        kb.button(text=m.name, callback_data=MasterCb(id=m.id))
    kb.adjust(1)
    return kb.as_markup()


def services_kb(services: list[Service]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in services:
        kb.button(text=f"{s.name} · {s.duration_min} мин · {fmt_price(s.price)}", callback_data=ServiceCb(id=s.id))
    kb.adjust(1)
    return kb.as_markup()


def days_kb(days: list[date]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for d in days:
        kb.button(text=fmt_day(d), callback_data=DayCb(iso=d.isoformat()))
    kb.adjust(3)
    return kb.as_markup()


def times_kb(slots: list[datetime]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in slots:
        kb.button(text=f"{s:%H:%M}", callback_data=TimeCb(ts=int(s.timestamp())))
    kb.adjust(4)
    return kb.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=ConfirmCb(ok=True))
    kb.button(text="✖️ Отмена", callback_data=ConfirmCb(ok=False))
    return kb.as_markup()


def payment_kb(confirmation_url: str, booking_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💳 Оплатить", url=confirmation_url))
    kb.row(InlineKeyboardButton(text="🔄 Я оплатил(а)", callback_data=PayCheckCb(id=booking_id).pack()))
    return kb.as_markup()


def my_bookings_kb(bookings: list[Booking]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for b in bookings:
        kb.button(text=f"Отменить №{b.id}", callback_data=CancelCb(id=b.id))
    kb.adjust(1)
    return kb.as_markup()


def booking_line(b: Booking, tz: ZoneInfo) -> str:
    mark = "⏳ ожидает оплаты · " if b.status.value == "awaiting_payment" else ""
    return f"№{b.id} · {mark}{b.service.name} · {fmt_dt(b.start_at, tz)}"


def companies_kb(companies: list[Company]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in companies:
        mark = "" if c.is_active else "🚫 "
        kb.button(text=f"{mark}{c.name}", callback_data=CompanyCb(id=c.id))
    kb.adjust(1)
    return kb.as_markup()


def deep_link(bot_username: str, slug: str) -> str:
    return f"https://t.me/{bot_username}?start={slug}"


def company_card(company: Company, admins: list[CompanyAdmin], bot_username: str) -> str:
    status = "✅ активна" if company.is_active else "🚫 отключена"
    admin_lines = "\n".join(f"  • {a.telegram_id}{' (владелец)' if a.is_owner else ''}" for a in admins) or "  — нет"
    return (
        f"<b>{company.name}</b>\n"
        f"Статус: {status}\n"
        f"Ссылка для клиентов: {deep_link(bot_username, company.slug)}\n\n"
        f"Админы:\n{admin_lines}"
    )


def fmt_stats(stats: dict, title: str) -> str:
    top = "\n".join(f"  {i + 1}. {name} — {count}" for i, (name, count) in enumerate(stats["top_services"])) or "  — нет данных"
    return (
        f"<b>Статистика — {title}</b>\n\n"
        f"📅 Предстоящих записей: {stats['upcoming_count']}\n"
        f"🆕 Новых за неделю: {stats['new_this_week']}\n"
        f"👥 Клиентов всего: {stats['unique_clients']}\n"
        f"💰 Выручка за 30 дней (по активным записям): {fmt_price(stats['revenue_month'])}\n\n"
        f"Популярные услуги за 30 дней:\n{top}"
    )
