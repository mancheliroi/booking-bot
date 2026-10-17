"""Инструменты главного разработчика: заводить компании и назначать им админов.

Редактировать услуги/тексты/мастеров конкретной компании супер-админ не может
напрямую — для этого он добавляет себя в её админы («👥 Админы» на карточке
компании) и дальше работает через обычный /admin, как любой владелец бизнеса.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import repo, ui
from app.admin_utils import slugify
from app.config import settings

router = Router(name="super_admin")
router.message.filter(F.from_user.id.func(settings.is_super_admin))
router.callback_query.filter(F.from_user.id.func(settings.is_super_admin))


class CompanyCardCb(CallbackData, prefix="cc"):
    id: int
    action: str  # open | admins | stats | toggle | back_to_card


class AdminRemoveCb(CallbackData, prefix="rmadm"):
    company_id: int
    telegram_id: int


class NewCompanyCb(CallbackData, prefix="newco"):
    pass


class Form(StatesGroup):
    input = State()


NOT_COMMAND = F.text & ~F.text.startswith("/")


def list_kb(companies) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in companies:
        mark = "" if c.is_active else "🚫 "
        kb.button(text=f"{mark}{c.name}", callback_data=CompanyCardCb(id=c.id, action="open"))
    kb.button(text="➕ Новая компания", callback_data=NewCompanyCb())
    kb.adjust(1)
    return kb.as_markup()


def card_kb(company) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 Админы", callback_data=CompanyCardCb(id=company.id, action="admins"))
    kb.button(text="📊 Статистика", callback_data=CompanyCardCb(id=company.id, action="stats"))
    kb.button(
        text="🚫 Деактивировать" if company.is_active else "▶️ Активировать",
        callback_data=CompanyCardCb(id=company.id, action="toggle"),
    )
    kb.button(text="⬅️ К списку", callback_data=CompanyCardCb(id=0, action="back_to_card"))
    kb.adjust(2, 1, 1)
    return kb.as_markup()


def admins_kb(company_id: int, admins) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for a in admins:
        label = f"✖️ {a.telegram_id}" + (" (владелец)" if a.is_owner else "")
        kb.button(text=label, callback_data=AdminRemoveCb(company_id=company_id, telegram_id=a.telegram_id))
    kb.button(text="➕ Добавить админа", callback_data=CompanyCardCb(id=company_id, action="add_admin"))
    kb.button(text="⬅️ К компании", callback_data=CompanyCardCb(id=company_id, action="open"))
    kb.adjust(1)
    return kb.as_markup()


@router.message(Command("companies"))
async def companies_list(message: Message, state: FSMContext, sessionmaker: async_sessionmaker) -> None:
    await state.clear()
    async with sessionmaker() as session:
        companies = await repo.list_companies(session)
    if not companies:
        await message.answer("Компаний пока нет. Создайте первую:", reply_markup=list_kb(companies))
        return
    await message.answer(f"Компании ({len(companies)}):", reply_markup=list_kb(companies))


@router.callback_query(NewCompanyCb.filter())
async def new_company_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Form.input)
    await state.update_data(step="new:name")
    await call.message.answer("Название компании (как увидят клиенты):")
    await call.answer()


@router.callback_query(CompanyCardCb.filter(F.action == "back_to_card"))
async def back_to_list(call: CallbackQuery, sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        companies = await repo.list_companies(session)
    await call.message.edit_text(f"Компании ({len(companies)}):", reply_markup=list_kb(companies))
    await call.answer()


@router.callback_query(CompanyCardCb.filter(F.action == "open"))
async def open_card(call: CallbackQuery, callback_data: CompanyCardCb, sessionmaker: async_sessionmaker, bot_username: str) -> None:
    async with sessionmaker() as session:
        company = await repo.get_company(session, callback_data.id)
        if company is None:
            await call.answer("Компания не найдена", show_alert=True)
            return
        admins = await repo.list_company_admins(session, company.id)
    await call.message.edit_text(ui.company_card(company, admins, bot_username), reply_markup=card_kb(company))
    await call.answer()


@router.callback_query(CompanyCardCb.filter(F.action == "toggle"))
async def toggle_company(call: CallbackQuery, callback_data: CompanyCardCb, sessionmaker: async_sessionmaker, bot_username: str) -> None:
    async with sessionmaker() as session:
        company = await repo.get_company(session, callback_data.id)
        company = await repo.set_company_active(session, callback_data.id, not company.is_active)
        admins = await repo.list_company_admins(session, company.id)
    await call.message.edit_text(ui.company_card(company, admins, bot_username), reply_markup=card_kb(company))
    await call.answer("Готово")


@router.callback_query(CompanyCardCb.filter(F.action == "stats"))
async def company_stats(call: CallbackQuery, callback_data: CompanyCardCb, sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        company = await repo.get_company(session, callback_data.id)
        stats = await repo.company_stats(session, callback_data.id)
    await call.message.answer(ui.fmt_stats(stats, company.name))
    await call.answer()


@router.callback_query(CompanyCardCb.filter(F.action == "admins"))
async def show_admins(call: CallbackQuery, callback_data: CompanyCardCb, sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        admins = await repo.list_company_admins(session, callback_data.id)
    await call.message.edit_text(
        "Админы компании. Нажмите на админа, чтобы убрать его.", reply_markup=admins_kb(callback_data.id, admins)
    )
    await call.answer()


@router.callback_query(CompanyCardCb.filter(F.action == "add_admin"))
async def add_admin_start(call: CallbackQuery, callback_data: CompanyCardCb, state: FSMContext) -> None:
    await state.set_state(Form.input)
    await state.update_data(step="add_admin", company_id=callback_data.id)
    await call.message.answer("Отправьте Telegram ID нового админа (число — его можно узнать у @userinfobot).")
    await call.answer()


@router.callback_query(AdminRemoveCb.filter())
async def remove_admin(call: CallbackQuery, callback_data: AdminRemoveCb, sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        await repo.remove_company_admin(session, callback_data.company_id, callback_data.telegram_id)
        admins = await repo.list_company_admins(session, callback_data.company_id)
    await call.message.edit_text(
        "Админы компании. Нажмите на админа, чтобы убрать его.",
        reply_markup=admins_kb(callback_data.company_id, admins),
    )
    await call.answer("Удалён")


@router.message(Form.input, NOT_COMMAND)
async def handle_input(message: Message, state: FSMContext, sessionmaker: async_sessionmaker, bot_username: str) -> None:
    data = await state.get_data()
    step = data["step"]
    value = (message.text or "").strip()

    if step == "new:name":
        if not 1 <= len(value) <= 150:
            await message.answer("Название должно быть от 1 до 150 символов.")
            return
        await state.update_data(step="new:slug", new_name=value)
        await message.answer(f"Короткий код для ссылки (латиница, цифры, без пробелов). Предложение: <code>{slugify(value)}</code>\nМожно отправить его же или своё.")

    elif step == "new:slug":
        slug = value.lower().strip()
        if not slug or not all(ch.isalnum() or ch == "_" for ch in slug) or len(slug) > 50:
            await message.answer("Код может содержать только латинские буквы, цифры и «_», до 50 символов.")
            return
        async with sessionmaker() as session:
            existing = await repo.get_company_by_slug(session, slug)
        if existing is not None:
            await message.answer("Такой код уже занят, предложите другой.")
            return
        await state.update_data(step="new:owner", new_slug=slug)
        await message.answer("Telegram ID владельца компании (её главного админа):")

    elif step == "new:owner":
        if not value.lstrip("-").isdigit():
            await message.answer("ID должен быть числом.")
            return
        owner_id = int(value)
        async with sessionmaker() as session:
            company = await repo.create_company(
                session, slug=data["new_slug"], name=data["new_name"], owner_telegram_id=owner_id
            )
        await state.clear()
        link = ui.deep_link(bot_username, company.slug)
        await message.answer(
            f"✅ Компания «{company.name}» создана.\n\n"
            f"Ссылка для клиентов: {link}\n"
            f"Владелец (ID {owner_id}) открывает /admin в этом же боте.",
        )

    elif step == "add_admin":
        if not value.lstrip("-").isdigit():
            await message.answer("ID должен быть числом.")
            return
        async with sessionmaker() as session:
            await repo.add_company_admin(session, data["company_id"], int(value))
            admins = await repo.list_company_admins(session, data["company_id"])
        await state.set_state(None)
        await message.answer("✅ Админ добавлен.", reply_markup=admins_kb(data["company_id"], admins))
