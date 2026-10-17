"""Клавиатуры и форматирование сообщений."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db import Booking, Service
from app.repo import as_utc

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

BTN_BOOK = "📅 Записаться"
BTN_MY = "📋 Мои записи"
BTN_CONTACTS = "ℹ️ Контакты"


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


def my_bookings_kb(bookings: list[Booking]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for b in bookings:
        kb.button(text=f"Отменить №{b.id}", callback_data=CancelCb(id=b.id))
    kb.adjust(1)
    return kb.as_markup()


def booking_line(b: Booking, tz: ZoneInfo) -> str:
    return f"№{b.id} · {b.service.name} · {fmt_dt(b.start_at, tz)}"
