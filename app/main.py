"""Сборка приложения и запуск polling."""

from aiogram import Bot
from aiogram import Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app import config
from app.database import init_db
from app.diagnostics import report_runtime_exception
from app.runtime import router

# Import handlers for their router decorators.
from app.handlers import admin as _admin_handlers
from app.handlers import customization as _customization_handlers
from app.handlers import decisions as _decision_handlers
from app.handlers import form as _form_handlers
from app.handlers import user as _user_handlers

async def main():

    bot = Bot(

        token=config.BOT_TOKEN,

        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    try:
        await init_db()
    except Exception as exc:
        await report_runtime_exception(
            bot,
            exc,
            context="startup:init_db",
        )
        raise

    dp = Dispatcher()

    dp.include_router(router)

    config.logger.info(
        "Evade Clan Bot запущен | db=%s | error_webhook=%s",
        config.DB_PATH,
        "enabled" if config.ERROR_WEBHOOK_URL else "disabled",
    )

    try:
        await dp.start_polling(bot)
    except Exception as exc:
        await report_runtime_exception(
            bot,
            exc,
            context="polling:fatal",
        )
        raise
