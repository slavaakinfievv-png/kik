"""Принятие и отклонение заявок администраторами."""

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery
import aiosqlite
import html

from app import config
from app.database import admin_allowed, get_application, make_decision
from app.diagnostics import report_runtime_exception
from app.questions import REJECTION_REASONS
from app.runtime import router
from app.services import refresh_staff_application_message
from app.ui import _decision_result_keyboard, contact_keyboard, rejection_keyboard, user_dashboard_keyboard

async def _finalize_decision_ui(
    callback: CallbackQuery,
    application_before,
    application_id: int,
) -> None:
    """Обновляет и исходную STAFF-карточку, и текущий экран администратора."""
    await refresh_staff_application_message(callback.bot, application_id)

    if not callback.message:
        return

    staff_message_id = application_before["staff_message_id"]
    is_original_staff_message = bool(
        staff_message_id
        and callback.message.chat.id == config.ADMIN_CHAT_ID
        and callback.message.message_id == int(staff_message_id)
    )
    if is_original_staff_message:
        return

    current = await get_application(application_id)
    if current is None:
        return

    if current["status"] == "accepted":
        text = f"✅ <b>Заявка #{application_id} принята.</b>"
    elif current["status"] == "rejected":
        reason = html.escape(current["rejection_reason"] or "Причина не указана")
        text = (
            f"❌ <b>Заявка #{application_id} отклонена.</b>\n\n"
            f"Причина: <b>{reason}</b>"
        )
    else:
        return

    try:
        await callback.message.edit_text(
            text,
            reply_markup=_decision_result_keyboard(application_id, str(current["status"])),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            config.logger.debug("failed to finalize admin decision UI", exc_info=True)
    except Exception:
        config.logger.debug("failed to finalize admin decision UI", exc_info=True)


@router.callback_query(
    F.data.startswith(
        "decision:accept:"
    )
)
async def accept_application(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer(
            "❌ У тебя нет прав для принятия анкет.",
            show_alert=True,
        )
        return

    try:
        application_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный номер анкеты.", show_alert=True)
        return

    application = await get_application(application_id)
    if application is None:
        await callback.answer("Анкета не найдена.", show_alert=True)
        return

    changed = await make_decision(
        application_id,
        "accepted",
        callback.from_user.id,
        callback.from_user.full_name,
    )
    if not changed:
        await _finalize_decision_ui(callback, application, application_id)
        await callback.answer(
            "Эта анкета уже была рассмотрена.",
            show_alert=True,
        )
        return

    await callback.answer("✅ Кандидат принят")

    user_id = int(application["user_id"])
    username = application["username"]
    full_name = application["full_name"]

    await _finalize_decision_ui(callback, application, application_id)

    # Решение всегда публикуем именно в STAFF. Ошибка отправки не откатывает уже принятое решение.
    try:
        decision_message = await callback.bot.send_message(
            config.ADMIN_CHAT_ID,
            f"✅ <b>Заявка #{application_id} принята.</b>\n"
            "🔐 Контактные данные отправлены администратору в личные сообщения."
        )
        async with aiosqlite.connect(config.DB_PATH) as db:
            await db.execute(
                "UPDATE applications SET decision_message_id = ? WHERE id = ?",
                (decision_message.message_id, application_id),
            )
            await db.commit()
    except Exception as exc:
        await report_runtime_exception(
            callback.bot,
            exc,
            context=f"publish_accepted_decision:{application_id}",
        )

    # Теперь, после принятия, данные получает только админ,
    # который нажал кнопку «Принять».
    private_admin_text = (
        f"✅ <b>Заявка #{application_id} принята</b>\n\n"
        "📌 Кандидат готов к дальнейшей связи.\n\n"
        f"👤 <b>{html.escape(full_name)}</b>\n"
        + (
            f"💬 @{html.escape(username)}\n"
            if username
            else "💬 Username отсутствует\n"
        )
        + f"🆔 ID: <code>{user_id}</code>"
    )

    try:
        await callback.bot.send_message(
            callback.from_user.id,
            private_admin_text,
            reply_markup=contact_keyboard(user_id, username),
        )
    except TelegramForbiddenError:
        config.logger.warning("admin %s has not opened private chat; application #%s", callback.from_user.id, application_id)
        if callback.message:
            await callback.message.answer(
                "⚠️ Не удалось отправить данные в личные сообщения. "
                "Открой личный чат с ботом и нажми /start."
            )
    except Exception as exc:
        await report_runtime_exception(
            callback.bot,
            exc,
            context=f"send_admin_contact:{application_id}",
        )
        if callback.message:
            await callback.message.answer(
                "⚠️ Не удалось отправить данные администратору в личные сообщения."
            )

    try:
        await callback.bot.send_message(
            user_id,
            "🎉 <b>ТВОЯ АНКЕТА ПРИНЯТА!</b>\n\n"
            "Поздравляем!\n\n"
            "В ближайшее время с тобой свяжется администрация клана.\n\n"
            "Теперь тебе доступны просмотр статуса и история заявок.",
            reply_markup=user_dashboard_keyboard("accepted"),
        )
    except Exception as exc:
        await report_runtime_exception(
            callback.bot,
            exc,
            context=f"notify_user_accepted:{application_id}",
        )


@router.callback_query(F.data.startswith("decision:reject:"))
async def reject_application(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("❌ У тебя нет прав для отклонения анкет.", show_alert=True)
        return

    try:
        application_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный номер анкеты.", show_alert=True)
        return

    application = await get_application(application_id)
    if application is None:
        await callback.answer("Анкета не найдена.", show_alert=True)
        return

    if application["status"] != "pending":
        await _finalize_decision_ui(callback, application, application_id)
        await callback.answer("Эта анкета уже рассмотрена.", show_alert=True)
        return

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"❌ <b>Почему отклоняем заявку #{application_id}?</b>",
            reply_markup=rejection_keyboard(application_id),
        )


@router.callback_query(F.data.startswith("reject_reason:"))
async def reject_reason(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    try:
        application_id = int(parts[1])
    except ValueError:
        await callback.answer("Некорректный номер.", show_alert=True)
        return

    reason_key = parts[2]
    application = await get_application(application_id)
    if application is None:
        await callback.answer("Анкета не найдена.", show_alert=True)
        return

    reason = REJECTION_REASONS.get(reason_key)
    if reason is None:
        await callback.answer("Некорректная причина.", show_alert=True)
        return

    changed = await make_decision(
        application_id,
        "rejected",
        callback.from_user.id,
        callback.from_user.full_name,
        reason,
    )
    if not changed:
        await _finalize_decision_ui(callback, application, application_id)
        await callback.answer("Эта анкета уже была рассмотрена.", show_alert=True)
        return

    await callback.answer("Заявка отклонена")

    await _finalize_decision_ui(callback, application, application_id)

    try:
        decision_message = await callback.bot.send_message(
            config.ADMIN_CHAT_ID,
            f"❌ <b>Анкета #{application_id} отклонена.</b>\n"
            f"Причина: <b>{html.escape(reason)}</b>"
        )
        async with aiosqlite.connect(config.DB_PATH) as db:
            await db.execute(
                "UPDATE applications SET decision_message_id = ? WHERE id = ?",
                (decision_message.message_id, application_id),
            )
            await db.commit()
    except Exception as exc:
        await report_runtime_exception(
            callback.bot,
            exc,
            context=f"publish_rejected_decision:{application_id}",
        )

    try:
        await callback.bot.send_message(
            int(application["user_id"]),
            "❌ <b>ТВОЯ АНКЕТА ОТКЛОНЕНА</b>\n\n"
            f"Причина: {html.escape(reason)}\n\n"
            "Ты можешь посмотреть статус заявки и подать новую анкету.",
            reply_markup=user_dashboard_keyboard("rejected"),
        )
    except Exception as exc:
        await report_runtime_exception(
            callback.bot,
            exc,
            context=f"notify_user_rejected:{application_id}",
        )
