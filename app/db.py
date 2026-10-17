import enum
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class BookingStatus(str, enum.Enum):
    active = "active"
    cancelled = "cancelled"
    # запись создана, слот удержан, но клиент ещё не оплатил
    awaiting_payment = "awaiting_payment"


class Company(Base):
    """Одна компания-арендатор. Бот общий, у каждой компании — своя витрина."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # часть диплинка t.me/<bot>?start=<slug>
    slug: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Расписание — у каждой компании может быть своё
    timezone: Mapped[str] = mapped_column(String(50), default="Europe/Moscow")
    work_start_hour: Mapped[int] = mapped_column(Integer, default=10)
    work_end_hour: Mapped[int] = mapped_column(Integer, default=20)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=60)
    days_ahead: Mapped[int] = mapped_column(Integer, default=7)
    # JSON-список номеров выходных дней недели (0 — пн ... 6 — вс)
    days_off_json: Mapped[str] = mapped_column(String(50), default="[6]")

    # Онлайн-оплата
    payment_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "yookassa" | "stripe"
    payment_shop_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_secret_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    require_prepayment: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    @property
    def days_off(self) -> list[int]:
        return json.loads(self.days_off_json)

    @days_off.setter
    def days_off(self, value: list[int]) -> None:
        self.days_off_json = json.dumps(value)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class CompanyAdmin(Base):
    """Кто может открыть /admin для конкретной компании."""

    __tablename__ = "company_admins"
    __table_args__ = (UniqueConstraint("company_id", "telegram_id", name="uq_company_admin"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)


class Master(Base):
    """Сотрудник компании — у каждого своё расписание записей."""

    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ClientSession(Base):
    """С какой компанией сейчас общается клиент — чтобы не просить диплинк на каждом шаге."""

    __tablename__ = "client_sessions"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    duration_min: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    # Скрытая услуга не видна клиентам, но старые записи на неё сохраняются
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    client_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(32))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    # Цена на момент записи — не меняется, даже если потом поменяют прайс услуги
    price: Mapped[int] = mapped_column(Integer, default=0)
    # Время хранится в UTC
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    status: Mapped[BookingStatus] = mapped_column(Enum(BookingStatus), default=BookingStatus.active)

    payment_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    service: Mapped[Service] = relationship(lazy="joined")
    master: Mapped[Master] = relationship(lazy="joined")


class BotText(Base):
    """Тексты бота, которые администратор компании меняет прямо в Telegram."""

    __tablename__ = "texts"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


DEFAULT_SERVICES = [
    ("Стрижка", 60, 1500),
    ("Окрашивание", 120, 4000),
    ("Укладка", 60, 1200),
    ("Консультация", 30, 0),
]

DEFAULT_MASTER_NAME = "Основной специалист"

# Ключ → (название для админа, текст по умолчанию)
EDITABLE_TEXTS = {
    "welcome": (
        "Приветствие",
        "Здравствуйте! 👋\n\nЯ помогу записаться на услугу: выберите время — и всё готово.",
    ),
    "booked": (
        "Сообщение после записи",
        "Ждём вас! Если планы изменятся — отмените запись в разделе «Мои записи».",
    ),
    "contacts": (
        "Контакты",
        "📍 Адрес: укажите адрес\n📞 Телефон: укажите телефон",
    ),
}


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine, sessionmaker: async_sessionmaker) -> None:
    """Только создаёт таблицы. Компании (и их услуги) заводит супер-админ через /companies."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
