"""Сервисные операции, связывающие БД и Telegram-сообщения."""

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from app import config
from app.database import _application_answers, get_application
from app.ui import admin_keyboard, build_staff_card

async def refresh_staff_application_message(bot: Bot, application_id: int) -> None:
    """Синхронизирует исходную STAFF-карточку с фактическим статусом из БД."""
    application = await get_application(application_id)
    if application is None or not application["staff_message_id"]:
        return

    answers = _application_answers(application)
    try:
        if answers is None:
            await bot.edit_message_reply_markup(
                chat_id=config.ADMIN_CHAT_ID,
                message_id=int(application["staff_message_id"]),
                reply_markup=None,
            )
            return

        await bot.edit_message_text(
            chat_id=config.ADMIN_CHAT_ID,
            message_id=int(application["staff_message_id"]),
            text=build_staff_card(
                int(application["id"]),
                answers,
                status=str(application["status"]),
                rejection_reason=application["rejection_reason"],
            ),
            reply_markup=(
                admin_keyboard(int(application["id"]))
                if application["status"] == "pending"
                else None
            ),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            config.logger.debug(
                "failed to refresh STAFF message for application #%s",
                application_id,
                exc_info=True,
            )
    except Exception:
        config.logger.debug(
            "failed to refresh STAFF message for application #%s",
            application_id,
            exc_info=True,
        )


async def disable_staff_application_keyboard(bot: Bot, application) -> None:
    """Совместимость: финализирует исходную карточку либо хотя бы убирает кнопки."""
    await refresh_staff_application_message(bot, int(application["id"]))
