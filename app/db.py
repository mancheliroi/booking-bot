import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class BookingStatus(str, enum.Enum):
    active = "active"
    cancelled = "cancelled"


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    duration_min: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    # Скрытая услуга не видна клиентам, но старые записи на неё сохраняются
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    client_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(32))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    # Время хранится в UTC
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[BookingStatus] = mapped_column(Enum(BookingStatus), default=BookingStatus.active)

    service: Mapped[Service] = relationship(lazy="joined")


class BotText(Base):
    """Тексты бота, которые администратор меняет прямо в Telegram."""

    __tablename__ = "texts"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


DEFAULT_SERVICES = [
    ("Стрижка", 60, 1500),
    ("Окрашивание", 120, 4000),
    ("Укладка", 60, 1200),
    ("Консультация", 30, 0),
]

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
    """Создаёт таблицы и заполняет услуги по умолчанию, если их нет."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sessionmaker() as session:
        exists = await session.scalar(select(Service.id).limit(1))
        if exists is None:
            session.add_all(Service(name=n, duration_min=d, price=p) for n, d, p in DEFAULT_SERVICES)
            await session.commit()
