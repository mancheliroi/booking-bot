import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String
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


DEFAULT_SERVICES = [
    ("Стрижка", 60, 1500),
    ("Окрашивание", 120, 4000),
    ("Укладка", 60, 1200),
    ("Консультация", 30, 0),
]


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine, sessionmaker: async_sessionmaker) -> None:
    """Создаёт таблицы и заполняет услуги по умолчанию, если их нет."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from sqlalchemy import select

    async with sessionmaker() as session:
        exists = await session.scalar(select(Service.id).limit(1))
        if exists is None:
            session.add_all(Service(name=n, duration_min=d, price=p) for n, d, p in DEFAULT_SERVICES)
            await session.commit()
