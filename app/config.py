from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки бота. Читаются из переменных окружения или файла .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    admin_ids: list[int] = Field(default_factory=list)
    database_url: str = "sqlite+aiosqlite:///./data/bookings.db"

    timezone: str = "Europe/Moscow"
    work_start_hour: int = 10
    work_end_hour: int = 20
    slot_minutes: int = 60
    days_ahead: int = 7
    # Выходные: 0 — понедельник ... 6 — воскресенье
    days_off: list[int] = Field(default_factory=lambda: [6])

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
