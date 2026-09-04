"""Глобальная диагностика, traceback и уведомления об ошибках."""

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import ErrorEvent
from datetime import datetime
from datetime import timezone
from time import monotonic
import asyncio
import hashlib
import html
import json
import traceback
import urllib.request

from app import config
from app.runtime import router

_error_last_notified: dict[str, float] = {}


def _error_update_context(event: ErrorEvent | None) -> dict[str, int | str | None]:
    context: dict[str, int | str | None] = {
        "update_id": None,
        "update_type": "internal",
        "user_id": None,
        "chat_id": None,
    }
    if event is None:
        return context

    update = event.update
    context["update_id"] = getattr(update, "update_id", None)

    callback = getattr(update, "callback_query", None)
    message = getattr(update, "message", None) or getattr(update, "edited_message", None)
    if callback is not None:
        context["update_type"] = "callback_query"
        context["user_id"] = getattr(getattr(callback, "from_user", None), "id", None)
        callback_message = getattr(callback, "message", None)
        context["chat_id"] = getattr(getattr(callback_message, "chat", None), "id", None)
    elif message is not None:
        context["update_type"] = "message"
        context["user_id"] = getattr(getattr(message, "from_user", None), "id", None)
        context["chat_id"] = getattr(getattr(message, "chat", None), "id", None)

    return context


def _build_error_payload(
    exception: Exception,
    context: str,
    event: ErrorEvent | None = None,
) -> tuple[str, dict[str, object]]:
    tb_text = "".join(
        traceback.format_exception(
            type(exception),
            exception,
            exception.__traceback__,
        )
    )
    fingerprint_source = f"{type(exception).__name__}|{context}|{tb_text}"
    error_id = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:12]
    update_context = _error_update_context(event)
    payload: dict[str, object] = {
        "error_id": error_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "context": context,
        "exception_type": type(exception).__name__,
        "exception": str(exception)[:1500],
        "traceback": tb_text[-12000:],
        **update_context,
    }
    return error_id, payload


def _post_error_webhook(payload: dict[str, object]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        config.ERROR_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        status = getattr(response, "status", 200)
        if status >= 400:
            raise RuntimeError(f"error webhook returned HTTP {status}")


async def _send_error_webhook(payload: dict[str, object]) -> None:
    if not config.ERROR_WEBHOOK_URL:
        return
    if not config.ERROR_WEBHOOK_URL.startswith("https://"):
        config.logger.warning("ERROR_WEBHOOK_URL пропущен: разрешён только https://")
        return
    try:
        await asyncio.to_thread(_post_error_webhook, payload)
    except Exception:
        config.logger.warning("Не удалось отправить диагностику в ERROR_WEBHOOK_URL", exc_info=True)


async def report_runtime_exception(
    bot: Bot,
    exception: Exception,
    *,
    context: str,
    event: ErrorEvent | None = None,
) -> str:
    error_id, payload = _build_error_payload(exception, context, event)
    config.logger.error(
        "runtime error id=%s context=%s",
        error_id,
        context,
        exc_info=(type(exception), exception, exception.__traceback__),
    )

    now = monotonic()
    previous = _error_last_notified.get(error_id, 0.0)
    if now - previous < max(0, config.ERROR_NOTIFY_COOLDOWN_SECONDS):
        return error_id
    _error_last_notified[error_id] = now

    await _send_error_webhook(payload)

    short_error = html.escape(str(exception)[:700] or type(exception).__name__)
    admin_text = (
        "🚨 <b>ОШИБКА БОТА</b>\n\n"
        f"ID: <code>{error_id}</code>\n"
        f"Контекст: <code>{html.escape(context)}</code>\n"
        f"Тип: <code>{html.escape(type(exception).__name__)}</code>\n"
        f"Ошибка: {short_error}\n\n"
        "Полный traceback записан в лог"
        + (" и отправлен во внешний webhook." if config.ERROR_WEBHOOK_URL else ".")
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text)
        except TelegramForbiddenError:
            config.logger.warning("Не удалось уведомить админа %s: личный чат с ботом закрыт", admin_id)
        except Exception:
            config.logger.warning("Не удалось уведомить админа %s об ошибке %s", admin_id, error_id, exc_info=True)

    return error_id


@router.error()
async def global_error_handler(event: ErrorEvent, bot: Bot):
    exception = event.exception
    if isinstance(exception, TelegramBadRequest) and "message is not modified" in str(exception).lower():
        config.logger.debug("Telegram message is not modified", exc_info=(type(exception), exception, exception.__traceback__))
        return

    await report_runtime_exception(
        bot,
        exception,
        context="unhandled_update",
        event=event,
    )
