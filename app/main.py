import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.config import settings
from app.db import init_db, make_engine, make_sessionmaker
from app.handlers import admin, user


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.bot_token:
        raise SystemExit("Не задан BOT_TOKEN — скопируйте .env.example в .env и укажите токен")

    if settings.database_url.startswith("sqlite"):
        Path("data").mkdir(exist_ok=True)

    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    await init_db(engine, sessionmaker)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])

    dp = Dispatcher()
    dp["sessionmaker"] = sessionmaker
    dp.include_routers(admin.router, user.router)

    try:
        await dp.start_polling(bot)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
