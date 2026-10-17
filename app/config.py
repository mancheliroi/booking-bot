from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки бота. Читаются из переменных окружения или файла .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    # Главный разработчик бота: управляет компаниями через /companies,
    # видит их насквозь, но услугами и текстами конкретной компании не рулит,
    # пока сам не добавит себя в её админы.
    admin_ids: list[int] = Field(default_factory=list)
    database_url: str = "sqlite+aiosqlite:///./data/bookings.db"

    # Значения по умолчанию для новой компании — дальше владелец меняет их себе через /admin
    timezone: str = "Europe/Moscow"
    work_start_hour: int = 10
    work_end_hour: int = 20
    slot_minutes: int = 60
    days_ahead: int = 7
    # Выходные: 0 — понедельник ... 6 — воскресенье
    days_off: list[int] = Field(default_factory=lambda: [6])

    # Сколько минут ждём оплату, прежде чем освободить слот
    payment_hold_minutes: int = 15

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def is_super_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.admin_ids


settings = Settings()
