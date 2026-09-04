import asyncio
import hashlib
import html
import json
import logging
import os
import traceback
import urllib.request
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from time import monotonic

import aiosqlite

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from dotenv import load_dotenv


# =========================================================
# НАСТРОЙКИ
# =========================================================

load_dotenv()

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
)
logger = logging.getLogger(__name__)

LOG_FILE = os.getenv("LOG_FILE", "bot.log").strip()
if LOG_FILE:
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logging.getLogger().addHandler(file_handler)
    except OSError:
        logger.warning("Не удалось открыть файл логов %s", LOG_FILE, exc_info=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_CHAT_ID = int(
    os.getenv("ADMIN_CHAT_ID", "0")
)

ADMIN_IDS = {
    int(item.strip())
    for item in os.getenv(
        "ADMIN_IDS",
        ""
    ).split(",")
    if item.strip().isdigit()
}

DB_PATH = os.getenv(
    "DB_PATH",
    "applications.db"
).strip()

ERROR_WEBHOOK_URL = os.getenv("ERROR_WEBHOOK_URL", "").strip()
try:
    ERROR_NOTIFY_COOLDOWN_SECONDS = int(
        os.getenv("ERROR_NOTIFY_COOLDOWN_SECONDS", "300")
    )
except ValueError:
    ERROR_NOTIFY_COOLDOWN_SECONDS = 300
    logger.warning(
        "Некорректный ERROR_NOTIFY_COOLDOWN_SECONDS; используется 300 секунд"
    )


if not BOT_TOKEN:
    raise RuntimeError(
        "В файле .env не указан BOT_TOKEN"
    )


if not ADMIN_CHAT_ID:
    raise RuntimeError(
        "В файле .env не указан ADMIN_CHAT_ID"
    )

if not ADMIN_IDS:
    raise RuntimeError(
        "В файле .env не указан ADMIN_IDS. "
        "Укажи Telegram ID администраторов через запятую."
    )


router = Router()


# =========================================================
# ДИАГНОСТИКА И ОШИБКИ
# =========================================================

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
        ERROR_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        status = getattr(response, "status", 200)
        if status >= 400:
            raise RuntimeError(f"error webhook returned HTTP {status}")


async def _send_error_webhook(payload: dict[str, object]) -> None:
    if not ERROR_WEBHOOK_URL:
        return
    if not ERROR_WEBHOOK_URL.startswith("https://"):
        logger.warning("ERROR_WEBHOOK_URL пропущен: разрешён только https://")
        return
    try:
        await asyncio.to_thread(_post_error_webhook, payload)
    except Exception:
        logger.warning("Не удалось отправить диагностику в ERROR_WEBHOOK_URL", exc_info=True)


async def report_runtime_exception(
    bot: Bot,
    exception: Exception,
    *,
    context: str,
    event: ErrorEvent | None = None,
) -> str:
    error_id, payload = _build_error_payload(exception, context, event)
    logger.error(
        "runtime error id=%s context=%s",
        error_id,
        context,
        exc_info=(type(exception), exception, exception.__traceback__),
    )

    now = monotonic()
    previous = _error_last_notified.get(error_id, 0.0)
    if now - previous < max(0, ERROR_NOTIFY_COOLDOWN_SECONDS):
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
        + (" и отправлен во внешний webhook." if ERROR_WEBHOOK_URL else ".")
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text)
        except TelegramForbiddenError:
            logger.warning("Не удалось уведомить админа %s: личный чат с ботом закрыт", admin_id)
        except Exception:
            logger.warning("Не удалось уведомить админа %s об ошибке %s", admin_id, error_id, exc_info=True)

    return error_id


@router.error()
async def global_error_handler(event: ErrorEvent, bot: Bot):
    exception = event.exception
    if isinstance(exception, TelegramBadRequest) and "message is not modified" in str(exception).lower():
        logger.debug("Telegram message is not modified", exc_info=(type(exception), exception, exception.__traceback__))
        return

    await report_runtime_exception(
        bot,
        exception,
        context="unhandled_update",
        event=event,
    )


# =========================================================
# ПОШАГОВАЯ АНКЕТА
# =========================================================

class ApplicationForm(StatesGroup):
    filling = State()
    confirming = State()


class AdminCustomization(StatesGroup):
    waiting_emoji = State()
    waiting_text = State()


QUESTIONS = [
    {
        "key": "name",
        "title": "Как к тебе обращаться?",
        "icon": "👤",
        "hint": "Напиши своё имя или удобное обращение.",
        "type": "text",
    },
    {
        "key": "roblox",
        "title": "Никнейм в Roblox",
        "icon": "🎮",
        "hint": "Укажи точный Roblox-ник.",
        "type": "text",
    },
    {
        "key": "level",
        "title": "Уровень в Evade",
        "icon": "📊",
        "hint": "Выбери готовый вариант или введи свой уровень.",
        "type": "level",
        "options": ["10", "25", "50", "100", "150"],
    },
    {
        "key": "experience",
        "title": "Как давно играешь в Evade?",
        "icon": "⏱",
        "hint": "Выбери вариант.",
        "type": "choice",
        "options": [
            "Меньше месяца",
            "1–6 месяцев",
            "6–12 месяцев",
            "1–2 года",
            "Более 2 лет",
        ],
    },
    {
        "key": "timezone",
        "title": "Твой часовой пояс",
        "icon": "🕐",
        "hint": "Выбери вариант.",
        "type": "choice",
        "options": ["UTC+0", "UTC+1", "UTC+2", "UTC+3", "UTC+4", "UTC+5", "UTC+6"],
    },
    {
        "key": "activity",
        "title": "Как часто ты играешь?",
        "icon": "📅",
        "hint": "Выбери вариант.",
        "type": "choice",
        "options": [
            "Каждый день",
            "3–5 раз в неделю",
            "1–2 раза в неделю",
            "Реже раза в неделю",
        ],
    },
    {
        "key": "skill",
        "title": "Оцени свой уровень игры от 1 до 10",
        "icon": "🎯",
        "hint": "Выбери оценку.",
        "type": "rating",
        "options": [str(i) for i in range(1, 11)],
    },
    {
        "key": "teamwork",
        "title": "Насколько хорошо ты играешь в команде?",
        "icon": "🤝",
        "hint": "Выбери оценку от 1 до 10.",
        "type": "rating",
        "options": [str(i) for i in range(1, 11)],
    },
    {
        "key": "clans",
        "title": "Состоял ли раньше в других кланах?",
        "icon": "🏆",
        "hint": "Выбери ответ.",
        "type": "yes_no",
    },
    {
        "key": "reason",
        "title": "Почему хочешь вступить именно в наш клан?",
        "icon": "💬",
        "hint": "Выбери готовый вариант или напиши свой.",
        "type": "choice",
        "options": [
            "Хочу играть с командой",
            "Хочу развиваться в Evade",
            "Хочу участвовать в клановых играх",
        ],
    },
    {
        "key": "rules",
        "title": "Готов ли соблюдать правила клана?",
        "icon": "📜",
        "hint": "Выбери ответ.",
        "type": "yes_no",
    },
]

# Ограничения защищают итоговые сообщения Telegram от переполнения.
ANSWER_MAX_LENGTH = {
    "name": 80,
    "roblox": 50,
    "level": 10,
    "experience": 150,
    "timezone": 50,
    "activity": 150,
    "skill": 2,
    "teamwork": 2,
    "clans": 100,
    "reason": 700,
    "rules": 100,
}


# =========================================================
# КАСТОМИЗАЦИЯ АНКЕТЫ
# =========================================================

DEFAULT_TEXT_STYLE = "standard"
TEXT_STYLES = {
    "standard": "Обычный",
    "bold": "Жирный",
    "italic": "Курсив",
    "underline": "Подчёркнутый",
    "mono": "Моноширинный",
    "spoiler": "Спойлер",
}

def custom_emoji_html(emoji_id, fallback):
    if not emoji_id:
        return fallback
    return f'<tg-emoji emoji-id="{html.escape(str(emoji_id), quote=True)}">{fallback}</tg-emoji>'

async def get_custom_value(key, default=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM bot_customization WHERE key = ?", (key,))
        row = await cur.fetchone()
    return row[0] if row else default

async def set_custom_value(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO bot_customization(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()

def apply_text_style(text, style):
    safe = html.escape(text)
    if style == "bold": return f"<b>{safe}</b>"
    if style == "italic": return f"<i>{safe}</i>"
    if style == "underline": return f"<u>{safe}</u>"
    if style == "mono": return f"<code>{safe}</code>"
    if style == "spoiler": return f"<tg-spoiler>{safe}</tg-spoiler>"
    return safe

async def get_question_config(question):
    return {
        "title": await get_custom_value(f"question:{question['key']}:title", question["title"]),
        "hint": await get_custom_value(f"question:{question['key']}:hint", question["hint"]),
        "emoji_id": await get_custom_value(f"question:{question['key']}:emoji_id"),
    }

async def build_question_visual(question):
    cfg = await get_question_config(question)
    style = await get_custom_value("question:text_style", DEFAULT_TEXT_STYLE)
    if style not in TEXT_STYLES: style = DEFAULT_TEXT_STYLE
    return (
        custom_emoji_html(cfg["emoji_id"], question.get("icon", "✨")),
        apply_text_style(cfg["title"], style),
        html.escape(cfg["hint"]),
    )

async def get_application_header() -> str:
    return await get_custom_value("application:header", "EVade CLAN")


def application_header_style_keyboard(current: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить заголовок", callback_data="custom:header:edit")],
        [InlineKeyboardButton(text="♻️ Сбросить на EVade CLAN", callback_data="custom:header:reset")],
        [InlineKeyboardButton(text="👁 Предпросмотр", callback_data="custom:header:preview")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:custom")],
    ])


def customization_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Заголовок анкеты", callback_data="custom:header")],
        [InlineKeyboardButton(text="✨ Premium Emoji", callback_data="custom:emoji")],
        [InlineKeyboardButton(text="📝 Тексты вопросов", callback_data="custom:texts")],
        [InlineKeyboardButton(text="🔤 Стиль текста", callback_data="custom:style")],
        [InlineKeyboardButton(text="👁 Превью вопросов", callback_data="custom:preview")],
        [InlineKeyboardButton(text="◀️ Назад в панель", callback_data="admin:home")],
    ])

def customization_questions_keyboard(prefix="custom:q"):
    rows=[[InlineKeyboardButton(text=f"{i+1}. {q['title']}", callback_data=f"{prefix}:{i}")] for i,q in enumerate(QUESTIONS)]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def custom_question_keyboard(index):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Заголовок", callback_data=f"custom:title:{index}"), InlineKeyboardButton(text="💡 Подсказка", callback_data=f"custom:hint:{index}")],
        [InlineKeyboardButton(text="✨ Emoji", callback_data=f"custom:emoji_for:{index}"), InlineKeyboardButton(text="👁 Превью", callback_data=f"custom:preview_question:{index}")],
        [InlineKeyboardButton(text="◀️ К вопросам", callback_data="custom:texts")],
    ])

def text_style_keyboard(current):
    rows=[]
    for key,label in TEXT_STYLES.items():
        rows.append([InlineKeyboardButton(text=("✅ " if key==current else "")+label, callback_data=f"custom:style:set:{key}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def user_main_keyboard(status: str | None = None):
    rows = [
        [InlineKeyboardButton(text="📋 Статус заявки", callback_data="user:status")],
        [InlineKeyboardButton(text="🗂 История заявок", callback_data="user:history")],
    ]

    # Новую анкету можно подать только впервые или после отказа.
    if status in {None, "rejected"}:
        rows.insert(0, [InlineKeyboardButton(text="📝 Подать анкету", callback_data="apply:start")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# Совместимость со старыми местами вызова.
def start_keyboard(status: str | None = None):
    return user_main_keyboard(status)


def cancel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="form:cancel",
                )
            ]
        ]
    )


def text_question_keyboard(step: int):
    rows = []
    if step > 0:
        rows.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="form:back",
            )
        )
    rows.append(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="form:cancel",
        )
    )
    return InlineKeyboardMarkup(inline_keyboard=[rows])


def choice_keyboard(step: int, options: list[str]):
    rows = []
    for index, option in enumerate(options):
        rows.append(
            [
                InlineKeyboardButton(
                    text=option,
                    callback_data=f"form:choose:{step}:{index}",
                )
            ]
        )

    nav = []
    if step > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="form:back",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="form:cancel",
        )
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def yes_no_keyboard(step: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да",
                    callback_data=f"form:choose:{step}:0",
                ),
                InlineKeyboardButton(
                    text="❌ Нет",
                    callback_data=f"form:choose:{step}:1",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="form:back",
                ),
                InlineKeyboardButton(
                    text="🚫 Отмена",
                    callback_data="form:cancel",
                ),
            ],
        ]
    )


def free_text_choice_keyboard(step: int):
    rows = [
        [
            InlineKeyboardButton(
                text="✍️ Ввести свой ответ",
                callback_data=f"form:custom:{step}",
            )
        ]
    ]
    if step > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="form:back",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="form:cancel",
                ),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="form:cancel",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirmation_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Отправить",
                    callback_data="form:submit",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить",
                    callback_data="form:edit",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="form:cancel",
                ),
            ],
        ]
    )


async def edit_fields_keyboard():
    rows = []
    for index, question in enumerate(QUESTIONS):
        cfg = await get_question_config(question)
        rows.append([
            InlineKeyboardButton(
                text=f"{index + 1}. {cfg['title'][:50]}",
                callback_data=f"form:edit_field:{index}",
            )
        ])
    rows.append([
        InlineKeyboardButton(text="⬅️ К проверке анкеты", callback_data="form:edit_back")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_keyboard(application_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Подробнее",
                    callback_data=f"application:details:{application_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=f"decision:accept:{application_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"decision:reject:{application_id}",
                ),
            ],
        ]
    )


def contact_keyboard(user_id: int, username: str | None):
    if username:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"💬 Написать @{username}",
                        url=f"https://t.me/{username}",
                    )
                ]
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Открыть профиль",
                    url=f"tg://user?id={user_id}",
                )
            ]
        ]
    )


async def build_application_text(answers: dict[str, str]) -> str:
    """Формирует анкету с актуальными настроенными заголовками вопросов."""
    lines = ["📝 <b>АНКЕТА НА ВСТУПЛЕНИЕ</b>", ""]

    for index, question in enumerate(QUESTIONS, start=1):
        cfg = await get_question_config(question)
        lines.append(f"<b>{index}. {html.escape(cfg['title'])}</b>")
        lines.append(html.escape(answers.get(question["key"], "")))
        lines.append("")

    return "\n".join(lines)


def build_staff_card(application_id: int, answers: dict[str, str]) -> str:
    """Короткая карточка заявки для STAFF без Telegram-контактов."""
    return (
        f"🆕 <b>НОВАЯ ЗАЯВКА #{application_id}</b>\n\n"
        f"🎮 <b>Roblox:</b> {html.escape(answers.get('roblox', '—'))}\n"
        f"📊 <b>Уровень:</b> {html.escape(answers.get('level', '—'))}\n"
        f"⏱ <b>Опыт:</b> {html.escape(answers.get('experience', '—'))}\n"
        f"📅 <b>Активность:</b> {html.escape(answers.get('activity', '—'))}\n"
        f"🎯 <b>Навык:</b> {html.escape(answers.get('skill', '—'))}/10\n"
        f"🤝 <b>Команда:</b> {html.escape(answers.get('teamwork', '—'))}/10\n"
        f"🏆 <b>Другие кланы:</b> {html.escape(answers.get('clans', '—'))}\n"
        f"📜 <b>Правила:</b> {html.escape(answers.get('rules', '—'))}\n\n"
        "💬 <b>Причина вступления:</b>\n"
        f"{html.escape(answers.get('reason', '—'))}\n\n"
        "━━━━━━━━━━━━━━\n"
        "⏳ <b>Статус:</b> ожидает решения"
    )


async def show_step(message: Message, state: FSMContext, step: int, edit: bool = False):
    question = QUESTIONS[step]
    total = len(QUESTIONS)

    icon, title, hint = await build_question_visual(question)
    header = await get_application_header()
    header_style = await get_custom_value("application:header_style", DEFAULT_TEXT_STYLE)
    if header_style not in TEXT_STYLES:
        header_style = DEFAULT_TEXT_STYLE
    styled_header = apply_text_style(header, header_style)
    text = (
        f"🎮 <b>{styled_header}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 <b>АНКЕТА — вопрос {step + 1} из {total}</b>\n\n"
        f"{icon} {title}\n\n"
        f"💡 {hint}\n\n"
    )

    if question["type"] == "text":
        text += "✍️ Отправь ответ одним сообщением."
        markup = text_question_keyboard(step)
    elif question["type"] == "level":
        text += "Выбери уровень:"
        markup = choice_keyboard(step, question["options"])
        markup.inline_keyboard.append(
            [InlineKeyboardButton(
                text="✍️ Ввести свой уровень",
                callback_data=f"form:custom:{step}",
            )]
        )
    elif question["type"] == "choice":
        text += "Выбери один вариант или введи свой ответ:"
        markup = choice_keyboard(step, question["options"])
        markup.inline_keyboard.insert(
            -1,
            [InlineKeyboardButton(
                text="✍️ Свой ответ",
                callback_data=f"form:custom:{step}",
            )],
        )
    elif question["type"] == "rating":
        text += "Выбери число от 1 до 10:"
        options = question["options"]
        rows = [options[i:i + 5] for i in range(0, len(options), 5)]
        inline = [
            [
                InlineKeyboardButton(
                    text=value,
                    callback_data=f"form:choose:{step}:{value}",
                )
                for value in row
            ]
            for row in rows
        ]
        if step > 0:
            inline.append([
                InlineKeyboardButton(text="⬅️ Назад", callback_data="form:back"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="form:cancel"),
            ])
        else:
            inline.append([
                InlineKeyboardButton(text="❌ Отмена", callback_data="form:cancel")
            ])
        markup = InlineKeyboardMarkup(inline_keyboard=inline)
    elif question["type"] == "yes_no":
        text += "Выбери ответ:"
        markup = yes_no_keyboard(step)
    else:
        raise RuntimeError(f"Неизвестный тип вопроса: {question['type']}")

    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def show_confirmation(
    message: Message,
    state: FSMContext,
    *,
    edit: bool = False,
):
    data = await state.get_data()
    answers = dict(data.get("answers", {}))

    header = await get_application_header()
    lines = [
        f"🎮 <b>{html.escape(header)}</b>",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "✅ <b>Проверь анкету перед отправкой</b>",
        "",
    ]
    for index, question in enumerate(QUESTIONS, start=1):
        cfg = await get_question_config(question)
        value = html.escape(answers.get(question["key"], ""))
        lines.append(f"<b>{index}. {html.escape(cfg['title'])}</b>")
        lines.append(value)
        lines.append("")

    lines.append("Если всё правильно — нажми «✅ Отправить».")
    text = "\n".join(lines)
    if edit:
        await message.edit_text(text, reply_markup=confirmation_keyboard())
    else:
        await message.answer(text, reply_markup=confirmation_keyboard())


# =========================================================
# СОЗДАНИЕ БАЗЫ ДАННЫХ
# =========================================================

async def init_db():

    async with aiosqlite.connect(
        DB_PATH
    ) as db:

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT NOT NULL,
                application_text TEXT NOT NULL,
                answers_json TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT,
                decided_by INTEGER,
                decided_at TEXT,
                reviewer_name TEXT,
                rejection_reason TEXT,
                decision_comment TEXT,
                staff_message_id INTEGER,
                decision_message_id INTEGER
            )
            """
        )

        cursor = await db.execute("PRAGMA table_info(applications)")
        columns = {row[1] for row in await cursor.fetchall()}
        migrations = {
            "answers_json": "ALTER TABLE applications ADD COLUMN answers_json TEXT",
            "updated_at": "ALTER TABLE applications ADD COLUMN updated_at TEXT",
            "decided_by": "ALTER TABLE applications ADD COLUMN decided_by INTEGER",
            "decided_at": "ALTER TABLE applications ADD COLUMN decided_at TEXT",
            "reviewer_name": "ALTER TABLE applications ADD COLUMN reviewer_name TEXT",
            "rejection_reason": "ALTER TABLE applications ADD COLUMN rejection_reason TEXT",
            "decision_comment": "ALTER TABLE applications ADD COLUMN decision_comment TEXT",
            "staff_message_id": "ALTER TABLE applications ADD COLUMN staff_message_id INTEGER",
            "decision_message_id": "ALTER TABLE applications ADD COLUMN decision_message_id INTEGER",
        }
        for column, statement in migrations.items():
            if column not in columns:
                await db.execute(statement)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS application_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                old_status TEXT,
                new_status TEXT NOT NULL,
                changed_by INTEGER,
                changed_by_name TEXT,
                comment TEXT,
                changed_at TEXT NOT NULL,
                FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_customization (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS custom_emojis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                emoji_id TEXT NOT NULL UNIQUE,
                fallback TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.commit()


# =========================================================
# ПРОВЕРКА:
# ЕСТЬ ЛИ УЖЕ НЕРАССМОТРЕННАЯ АНКЕТА
# =========================================================

async def get_latest_user_application(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, status, created_at, updated_at, rejection_reason, decision_comment
            FROM applications
            WHERE user_id = ?
            ORDER BY CASE status WHEN 'accepted' THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        return await cursor.fetchone()


async def get_user_applications(user_id: int, limit: int = 15):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, status, created_at, updated_at, rejection_reason, decision_comment
            FROM applications
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return await cursor.fetchall()


async def has_pending_application(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT 1
            FROM applications
            WHERE user_id = ?
              AND status = 'pending'
            LIMIT 1
            """,
            (user_id,),
        )
        return await cursor.fetchone() is not None


async def get_submission_block_status(user_id: int) -> str | None:
    """Возвращает accepted/pending, если новую анкету подавать нельзя."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT status
            FROM applications
            WHERE user_id = ?
              AND status IN ('accepted', 'pending')
            ORDER BY CASE status WHEN 'accepted' THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


# =========================================================
# СОХРАНЕНИЕ АНКЕТЫ
# =========================================================

async def save_application(
    message: Message
):

    async with aiosqlite.connect(
        DB_PATH
    ) as db:

        cursor = await db.execute(
            """
            INSERT INTO applications (

                user_id,
                username,
                full_name,
                application_text,
                status,
                created_at

            )

            VALUES (
                ?, ?, ?, ?,
                'pending', ?
            )
            """,

            (
                message.from_user.id,
                message.from_user.username,
                message.from_user.full_name,
                message.text,

                datetime.now(
                    timezone.utc
                ).isoformat()
            )
        )

        await db.commit()

        return int(cursor.lastrowid)


# =========================================================
# ПОЛУЧЕНИЕ АНКЕТЫ ИЗ БАЗЫ
# =========================================================

async def get_application(application_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT *
            FROM applications
            WHERE id = ?
            """,
            (application_id,),
        )
        return await cursor.fetchone()


async def disable_staff_application_keyboard(bot: Bot, application) -> None:
    """Убирает кнопки решения с исходной карточки заявки в STAFF."""
    staff_message_id = application["staff_message_id"]
    if not staff_message_id:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=ADMIN_CHAT_ID,
            message_id=int(staff_message_id),
            reply_markup=None,
        )
    except Exception:
        logger.debug(
            "failed to disable STAFF keyboard for application #%s",
            application["id"],
            exc_info=True,
        )


# =========================================================
# СОХРАНЕНИЕ РЕШЕНИЯ
# =========================================================

async def make_decision(
    application_id: int,
    status: str,
    admin_id: int,
    admin_name: str | None = None,
    rejection_reason: str | None = None,
    decision_comment: str | None = None,
):
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "SELECT status FROM applications WHERE id = ?",
            (application_id,),
        )
        current = await cursor.fetchone()
        if not current:
            await db.rollback()
            return False

        old_status = current["status"]
        if old_status != "pending":
            await db.rollback()
            return False

        cursor = await db.execute(
            """
            UPDATE applications
            SET
                status = ?,
                updated_at = ?,
                decided_by = ?,
                decided_at = ?,
                reviewer_name = ?,
                rejection_reason = ?,
                decision_comment = ?
            WHERE id = ?
            AND status = 'pending'
            """,
            (
                status,
                now,
                admin_id,
                now,
                admin_name,
                rejection_reason,
                decision_comment,
                application_id,
            ),
        )
        if cursor.rowcount != 1:
            await db.rollback()
            return False

        await db.execute(
            """
            INSERT INTO application_history (
                application_id, old_status, new_status,
                changed_by, changed_by_name, comment, changed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                application_id,
                old_status,
                status,
                admin_id,
                admin_name,
                decision_comment or rejection_reason,
                now,
            ),
        )
        await db.commit()

    logger.info(
        "application #%s: %s -> %s by admin %s (%s)",
        application_id, old_status, status, admin_id, admin_name or "unknown",
    )
    return True


# =========================================================
# ПРОВЕРКА ПРАВ АДМИНА
# =========================================================

def admin_allowed(
    user_id: int
):

    return user_id in ADMIN_IDS


# =========================================================
# УПРАВЛЕНИЕ ЗАЯВКАМИ STAFF
# =========================================================


def is_staff_admin(message: Message) -> bool:
    return admin_allowed(message.from_user.id)


@router.message(Command("apps"))
async def cmd_apps(message: Message):
    if not is_staff_admin(message):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM applications")
        total = int((await cursor.fetchone())[0])
        cursor = await db.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'pending'"
        )
        pending = int((await cursor.fetchone())[0])
        cursor = await db.execute("SELECT MAX(id) FROM applications")
        last_id = int((await cursor.fetchone())[0] or 0)

    await message.answer(
        "📊 <b>Статистика заявок</b>\n\n"
        f"📋 Всего заявок в базе: <b>{total}</b>\n"
        f"⏳ На рассмотрении: <b>{pending}</b>\n"
        f"🔢 Последний номер: <b>{last_id}</b>\n"
        f"➡️ Следующий номер: <b>{last_id + 1}</b>"
    )


@router.message(Command("clearapps"))
async def cmd_clearapps(message: Message):
    if not is_staff_admin(message):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "<code>/clearapps 5</code> — удалить 5 последних заявок и все связанные сообщения бота\n"
            "<code>/clearapps all</code> — удалить все заявки, связанные сообщения и обнулить счётчик"
        )
        return

    value = parts[1].strip().lower()

    async with aiosqlite.connect(DB_PATH) as db:
        if value == "all":
            cursor = await db.execute(
                "SELECT id, staff_message_id, decision_message_id "
                "FROM applications ORDER BY id DESC"
            )
            rows = await cursor.fetchall()

            deleted_messages = 0
            attempted_ids = set()
            for _, staff_message_id, decision_message_id in rows:
                for message_id in (staff_message_id, decision_message_id):
                    if not message_id or int(message_id) in attempted_ids:
                        continue
                    attempted_ids.add(int(message_id))
                    try:
                        await message.bot.delete_message(
                            chat_id=ADMIN_CHAT_ID,
                            message_id=int(message_id),
                        )
                        deleted_messages += 1
                    except Exception:
                        logger.debug("failed to delete STAFF message %s", message_id, exc_info=True)

            await db.execute("DELETE FROM application_history")
            await db.execute("DELETE FROM applications")
            await db.execute("DELETE FROM sqlite_sequence WHERE name='applications'")
            await db.execute("DELETE FROM sqlite_sequence WHERE name='application_history'")
            await db.commit()

            await message.answer(
                "🗑 <b>Все заявки удалены.</b>\n\n"
                f"Удалено связанных сообщений бота: <b>{deleted_messages}</b>\n"
                "🔄 Счётчик обнулён. Следующая заявка будет <b>#1</b>."
            )
            return

        try:
            count = int(value)
        except ValueError:
            await message.answer("❌ Укажи целое число или <code>all</code>.")
            return

        if count <= 0:
            await message.answer("❌ Количество должно быть больше 0.")
            return

        cursor = await db.execute(
            "SELECT id, staff_message_id, decision_message_id "
            "FROM applications ORDER BY id DESC LIMIT ?",
            (count,),
        )
        rows = await cursor.fetchall()

        deleted_messages = 0
        deleted_rows = 0
        attempted_ids = set()

        for internal_id, staff_message_id, decision_message_id in rows:
            for message_id in (staff_message_id, decision_message_id):
                if not message_id or int(message_id) in attempted_ids:
                    continue
                attempted_ids.add(int(message_id))
                try:
                    await message.bot.delete_message(
                        chat_id=ADMIN_CHAT_ID,
                        message_id=int(message_id),
                    )
                    deleted_messages += 1
                except Exception:
                    logger.debug("failed to delete STAFF message %s", message_id, exc_info=True)

            await db.execute(
                "DELETE FROM application_history WHERE application_id = ?",
                (internal_id,),
            )
            await db.execute(
                "DELETE FROM applications WHERE id = ?",
                (internal_id,),
            )
            deleted_rows += 1

        await db.commit()

    await message.answer(
        f"🗑 Удалено заявок: <b>{deleted_rows}</b>\n"
        f"Удалено связанных сообщений бота: <b>{deleted_messages}</b>\n\n"
        "🔢 Счётчик не обнулён. Для полного сброса используй <code>/clearapps all</code>."
    )


@router.message(Command("clearstaff"))
async def cmd_clearstaff(message: Message):
    if not is_staff_admin(message):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, staff_message_id, decision_message_id FROM applications"
        )
        rows = await cursor.fetchall()

    deleted = 0
    failed = 0
    delete_results: dict[int, bool] = {}

    async def delete_once(message_id: int) -> bool:
        nonlocal deleted, failed
        if message_id in delete_results:
            return delete_results[message_id]
        try:
            await message.bot.delete_message(ADMIN_CHAT_ID, message_id)
            deleted += 1
            result = True
        except TelegramBadRequest as exc:
            if "message to delete not found" in str(exc).lower():
                result = True
            else:
                failed += 1
                logger.warning("failed to delete STAFF message %s", message_id, exc_info=True)
                result = False
        except Exception:
            failed += 1
            logger.warning("failed to delete STAFF message %s", message_id, exc_info=True)
            result = False
        delete_results[message_id] = result
        return result

    async with aiosqlite.connect(DB_PATH) as db:
        for application_id, staff_message_id, decision_message_id in rows:
            if staff_message_id and await delete_once(int(staff_message_id)):
                await db.execute(
                    "UPDATE applications SET staff_message_id = NULL WHERE id = ?",
                    (application_id,),
                )
            if decision_message_id and await delete_once(int(decision_message_id)):
                await db.execute(
                    "UPDATE applications SET decision_message_id = NULL WHERE id = ?",
                    (application_id,),
                )
        await db.commit()

    try:
        await message.delete()
    except Exception:
        logger.debug("failed to delete /clearstaff command message", exc_info=True)

    await message.answer(
        f"🧹 Удалено сообщений бота из STAFF: <b>{deleted}</b>\n"
        f"⚠️ Не удалось удалить: <b>{failed}</b>\n"
        "Заявки и счётчик базы не изменены. Неудалённые ID сохранены для повторной попытки."
    )


@router.message(Command("resetcounter"))
async def cmd_resetcounter(message: Message):
    if not is_staff_admin(message):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor=await db.execute("SELECT COUNT(*) FROM applications")
        total=int((await cursor.fetchone())[0])
        if total:
            await message.answer(
                "⚠️ Нельзя обнулить счётчик, пока в базе есть заявки.\n\n"
                "Сначала используй <code>/clearapps all</code>."
            )
            return
        await db.execute("DELETE FROM sqlite_sequence WHERE name='applications'")
        await db.commit()

    await message.answer(
        "🔄 <b>Счётчик заявок обнулён.</b>\n"
        "Следующая заявка получит номер <b>#1</b>."
    )



# =========================================================
# АДМИН-ПАНЕЛЬ
# =========================================================

def admin_section_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📥 Новые", callback_data="admin:pending"),
                InlineKeyboardButton(text="✅ Принятые", callback_data="admin:accepted"),
            ],
            [
                InlineKeyboardButton(text="❌ Отклонённые", callback_data="admin:rejected"),
                InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
            ],
            [
                InlineKeyboardButton(text="🔎 Поиск", callback_data="admin:search_help"),
                InlineKeyboardButton(text="🧹 Управление", callback_data="admin:manage_help"),
            ],
            [InlineKeyboardButton(text="🎨 Кастомизация анкеты", callback_data="admin:custom")],
        ]
    )


def user_history_keyboard(rows):
    buttons = []
    for row in rows:
        status_map = {
            "pending": "🕐",
            "accepted": "✅",
            "rejected": "❌",
        }
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_map.get(row['status'], '📋')} Заявка #{row['id']}",
                callback_data=f"user:application:{row['id']}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def user_dashboard_keyboard(status: str | None = None):
    return user_main_keyboard(status)


def application_roblox_name(row) -> str:
    """Безопасное отображаемое имя заявки без Telegram-контактов."""
    try:
        raw = row["answers_json"]
    except (KeyError, IndexError, TypeError):
        raw = None

    if raw:
        try:
            answers = json.loads(raw)
            roblox = str(answers.get("roblox", "")).strip()
            if roblox:
                return roblox[:50]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    return "без Roblox-ника"


def admin_status_list_keyboard(rows, status: str):
    buttons = []
    for row in rows:
        roblox = application_roblox_name(row)
        buttons.append([
            InlineKeyboardButton(
                text=f"📋 Заявка #{row['id']} — {roblox}",
                callback_data=f"application:details:{row['id']}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin:{status}"),
        InlineKeyboardButton(text="⬅️ Панель", callback_data="admin:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_panel_keyboard():
    return admin_section_keyboard()


def admin_back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в панель", callback_data="admin:home")]
        ]
    )


def pending_list_keyboard(rows):
    buttons = []
    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                text=f"📋 Заявка #{row['id']}",
                callback_data=f"application:details:{row['id']}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:pending"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def rejection_keyboard(application_id: int):
    reasons = [
        ("Недостаточный уровень", "low_level"),
        ("Мало играет", "low_activity"),
        ("Не подходит", "not_suitable"),
        ("Нарушения", "rules"),
        ("Другая причина", "other"),
    ]
    rows = []
    for title, key in reasons:
        rows.append([
            InlineKeyboardButton(
                text=title,
                callback_data=f"reject_reason:{application_id}:{key}",
            )
        ])
    rows.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"application:details:{application_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


REJECTION_REASONS = {
    "low_level": "Недостаточный уровень",
    "low_activity": "Недостаточная активность",
    "not_suitable": "Не подходит по требованиям",
    "rules": "Проблемы с соблюдением правил",
    "other": "Другая причина / решение администрации",
}


async def render_admin_panel(message: Message):
    if not admin_allowed(message.from_user.id):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM applications WHERE status='pending'")
        pending = int((await cur.fetchone())[0])
        cur = await db.execute("SELECT COUNT(*) FROM applications WHERE status='accepted'")
        accepted = int((await cur.fetchone())[0])
        cur = await db.execute("SELECT COUNT(*) FROM applications WHERE status='rejected'")
        rejected = int((await cur.fetchone())[0])

    text = (
        "🛠 <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n\n"
        f"📥 Новые: <b>{pending}</b>\n"
        f"✅ Принято: <b>{accepted}</b>\n"
        f"❌ Отклонено: <b>{rejected}</b>\n\n"
        "Выбери раздел:"
    )
    await message.answer(text, reply_markup=admin_section_keyboard())


@router.callback_query(F.data == "admin:home")
async def admin_home(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM applications WHERE status='pending'")
        pending = int((await cur.fetchone())[0])
        cur = await db.execute("SELECT COUNT(*) FROM applications WHERE status='accepted'")
        accepted = int((await cur.fetchone())[0])
        cur = await db.execute("SELECT COUNT(*) FROM applications WHERE status='rejected'")
        rejected = int((await cur.fetchone())[0])

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "🛠 <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n\n"
            f"📥 Новые: <b>{pending}</b>\n"
            f"✅ Принято: <b>{accepted}</b>\n"
            f"❌ Отклонено: <b>{rejected}</b>\n\n"
            "Выбери раздел:",
            reply_markup=admin_section_keyboard(),
        )


@router.callback_query(F.data == "admin:pending")
async def admin_pending(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id FROM applications WHERE status='pending' ORDER BY id DESC LIMIT 15"
        )
        rows = await cur.fetchall()

    await callback.answer()
    if not callback.message:
        return

    if not rows:
        await callback.message.edit_text(
            "📥 <b>НОВЫЕ ЗАЯВКИ</b>\n\n"
            "✅ На рассмотрении сейчас нет заявок.",
            reply_markup=admin_back_keyboard(),
        )
        return

    await callback.message.edit_text(
        "📥 <b>НОВЫЕ ЗАЯВКИ</b>\n\n"
        "Выбери заявку для просмотра:",
        reply_markup=pending_list_keyboard(rows),
    )


async def render_admin_status_list(callback: CallbackQuery, status: str, title: str):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, answers_json FROM applications WHERE status = ? ORDER BY id DESC LIMIT 15",
            (status,),
        )
        rows = await cur.fetchall()

    await callback.answer()
    if not callback.message:
        return

    if not rows:
        await callback.message.edit_text(
            f"{title}\n\nЗдесь пока нет заявок.",
            reply_markup=admin_back_keyboard(),
        )
        return

    await callback.message.edit_text(
        f"{title}\n\nВыбери заявку:",
        reply_markup=admin_status_list_keyboard(rows, status),
    )


@router.callback_query(F.data == "admin:accepted")
async def admin_accepted(callback: CallbackQuery):
    await render_admin_status_list(callback, "accepted", "✅ <b>ПРИНЯТЫЕ ЗАЯВКИ</b>")


@router.callback_query(F.data == "admin:rejected")
async def admin_rejected(callback: CallbackQuery):
    await render_admin_status_list(callback, "rejected", "❌ <b>ОТКЛОНЁННЫЕ ЗАЯВКИ</b>")


@router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM applications")
        total = int((await cur.fetchone())[0])
        cur = await db.execute("SELECT COUNT(*) FROM applications WHERE status='pending'")
        pending = int((await cur.fetchone())[0])
        cur = await db.execute("SELECT COUNT(*) FROM applications WHERE status='accepted'")
        accepted = int((await cur.fetchone())[0])
        cur = await db.execute("SELECT COUNT(*) FROM applications WHERE status='rejected'")
        rejected = int((await cur.fetchone())[0])

    closed = accepted + rejected
    rate = round(accepted / closed * 100, 1) if closed else 0

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "📊 <b>СТАТИСТИКА</b>\n\n"
            f"📋 Всего: <b>{total}</b>\n"
            f"⏳ На рассмотрении: <b>{pending}</b>\n"
            f"✅ Принято: <b>{accepted}</b>\n"
            f"❌ Отклонено: <b>{rejected}</b>\n"
            f"📈 Процент принятия: <b>{rate}%</b>",
            reply_markup=admin_back_keyboard(),
        )


@router.callback_query(F.data == "admin:search_help")
async def admin_search_help(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "🔎 <b>ПОИСК ЗАЯВОК</b>\n\n"
            "Для поиска используй команду:\n\n"
            "<code>/find Player123</code> — Roblox-ник\n"
            "<code>/find 42</code> — номер заявки\n\n"
            "Результат появится отдельным сообщением.",
            reply_markup=admin_back_keyboard(),
        )


@router.callback_query(F.data == "admin:manage_help")
async def admin_manage_help(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "🧹 <b>УПРАВЛЕНИЕ ЗАЯВКАМИ</b>\n\n"
            "<code>/clearapps 5</code> — удалить 5 последних\n"
            "<code>/clearapps all</code> — удалить всё и обнулить счётчик\n"
            "<code>/clearstaff</code> — убрать сообщения бота из STAFF\n"
            "<code>/resetcounter</code> — обнулить счётчик при пустой базе",
            reply_markup=admin_back_keyboard(),
        )


@router.callback_query(F.data == "admin:custom")
async def admin_custom(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True); return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "🎨 <b>КАСТОМИЗАЦИЯ АНКЕТЫ</b>\n\nВыбери, что изменить:",
            reply_markup=customization_keyboard(),
        )

@router.callback_query(F.data == "custom:header")
async def custom_header_menu(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    current = await get_application_header()
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "🎮 <b>ЗАГОЛОВОК АНКЕТЫ</b>\n\n"
            f"Текущий заголовок: <b>{html.escape(current)}</b>\n\n"
            "Именно этот текст будет отображаться в шапке каждого вопроса анкеты.",
            reply_markup=application_header_style_keyboard(current),
        )


@router.callback_query(F.data == "custom:header:edit")
async def custom_header_edit(callback: CallbackQuery, state: FSMContext):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state(AdminCustomization.waiting_text)
    await state.update_data(custom_field="application_header")
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "✏️ <b>Новый заголовок анкеты</b>\n\n"
            "Отправь одним сообщением текст, например:\n"
            "<code>EVade CLAN</code>\n\n"
            "Можно использовать обычный текст и emoji.",
        )


@router.callback_query(F.data == "custom:header:reset")
async def custom_header_reset(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await set_custom_value("application:header", "EVade CLAN")
    await callback.answer("Сброшено ✅")
    if callback.message:
        await callback.message.edit_text(
            "✅ <b>Заголовок сброшен</b>\n\nТекущий заголовок: <b>EVade CLAN</b>",
            reply_markup=application_header_style_keyboard("EVade CLAN"),
        )


@router.callback_query(F.data == "custom:header:preview")
async def custom_header_preview(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    header = await get_application_header()
    icon, title, hint = await build_question_visual(QUESTIONS[0])
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"👁 <b>ПРЕДПРОСМОТР</b>\n\n"
            f"🎮 <b>{html.escape(header)}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📝 <b>АНКЕТА — вопрос 1 из {len(QUESTIONS)}</b>\n\n"
            f"{icon} {title}\n\n"
            f"💡 {hint}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ К заголовку", callback_data="custom:header")]
            ]),
        )


@router.callback_query(F.data == "custom:emoji")
async def custom_emoji_menu(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True); return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text("✨ <b>PREMIUM EMOJI</b>\n\nДобавляй Premium Emoji прямо сообщением и назначай его на вопросы.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить", callback_data="emoji:add")],
            [InlineKeyboardButton(text="📋 Мои emoji", callback_data="emoji:list")],
            [InlineKeyboardButton(text="🎯 Назначить на вопрос", callback_data="emoji:assign")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:custom")],
        ]))

@router.callback_query(F.data == "emoji:add")
async def emoji_add(callback: CallbackQuery, state: FSMContext):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True); return
    await state.set_state(AdminCustomization.waiting_emoji)
    await callback.answer()
    await callback.message.edit_text("✨ <b>Отправь Premium Emoji</b>\n\nОтправь одним сообщением именно кастомный emoji из Telegram.")

@router.message(AdminCustomization.waiting_emoji)
async def emoji_receive(message: Message, state: FSMContext):
    if not admin_allowed(message.from_user.id): await state.clear(); return
    entity = next((e for e in (message.entities or []) if e.type == "custom_emoji" and e.custom_emoji_id), None)
    if entity is None or not message.text:
        await message.answer("❌ Кастомный emoji не найден. Отправь Premium Emoji ещё раз."); return
    fallback = message.text[entity.offset:entity.offset+entity.length] or "✨"
    name=f"emoji_{entity.custom_emoji_id[-6:]}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO custom_emojis(name,emoji_id,fallback,created_at) VALUES(?,?,?,?)", (name,entity.custom_emoji_id,fallback,datetime.now(timezone.utc).isoformat()))
        await db.commit()
    await state.clear()
    await message.answer(f"✅ <b>Emoji сохранён</b>\n\n{fallback}\nID: <code>{html.escape(entity.custom_emoji_id)}</code>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ В Premium Emoji", callback_data="custom:emoji")]]))

@router.callback_query(F.data == "emoji:list")
async def emoji_list(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        rows=await (await db.execute("SELECT * FROM custom_emojis ORDER BY id DESC")).fetchall()
    text="📋 <b>МОИ PREMIUM EMOJI</b>\n\n"
    text += "\n".join(f"{r['fallback']} <b>{html.escape(r['name'])}</b> — <code>{html.escape(r['emoji_id'])}</code>" for r in rows) if rows else "Пока пусто."
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="custom:emoji")]]))

@router.callback_query(F.data == "emoji:assign")
async def emoji_assign(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    await callback.answer(); await callback.message.edit_text("🎯 <b>Выбери вопрос:</b>", reply_markup=customization_questions_keyboard("emoji:q"))

@router.callback_query(F.data.startswith("emoji:q:"))
async def emoji_question(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    index=int(callback.data.split(":")[-1]); q=QUESTIONS[index]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        rows=await (await db.execute("SELECT * FROM custom_emojis ORDER BY id DESC")).fetchall()
    buttons=[[InlineKeyboardButton(text=f"{r['fallback']} {r['name']}", callback_data=f"emoji:set:{index}:{r['id']}")] for r in rows]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="emoji:assign")])
    await callback.answer(); await callback.message.edit_text(f"🎯 <b>{html.escape(q['title'])}</b>\n\nВыбери emoji:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("emoji:set:"))
async def emoji_set(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    _,_,index_s,dbid=callback.data.split(":"); index=int(index_s)
    async with aiosqlite.connect(DB_PATH) as db:
        row=await (await db.execute("SELECT emoji_id FROM custom_emojis WHERE id=?", (int(dbid),))).fetchone()
    if not row: await callback.answer("Emoji не найден", show_alert=True); return
    await set_custom_value(f"question:{QUESTIONS[index]['key']}:emoji_id", row[0])
    await callback.answer("Назначен ✅"); await callback.message.edit_text("✅ Emoji назначен.", reply_markup=custom_question_keyboard(index))

@router.callback_query(F.data == "custom:texts")
async def custom_texts(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    await callback.answer(); await callback.message.edit_text("📝 <b>ТЕКСТЫ ВОПРОСОВ</b>\n\nВыбери вопрос:", reply_markup=customization_questions_keyboard())

@router.callback_query(F.data.startswith("custom:q:"))
async def custom_q(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    index=int(callback.data.split(":")[-1]); q=QUESTIONS[index]; cfg=await get_question_config(q)
    await callback.answer(); await callback.message.edit_text(f"📝 <b>Вопрос {index+1}</b>\n\n<b>Заголовок:</b> {html.escape(cfg['title'])}\n<b>Подсказка:</b> {html.escape(cfg['hint'])}", reply_markup=custom_question_keyboard(index))

@router.callback_query(F.data.startswith("custom:title:"))
async def custom_title(callback: CallbackQuery, state: FSMContext):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    index=int(callback.data.split(":")[-1]); await state.set_state(AdminCustomization.waiting_text); await state.update_data(custom_field="title", custom_question=index); await callback.answer(); await callback.message.edit_text("✏️ Отправь новый заголовок одним сообщением.")

@router.callback_query(F.data.startswith("custom:hint:"))
async def custom_hint(callback: CallbackQuery, state: FSMContext):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    index=int(callback.data.split(":")[-1]); await state.set_state(AdminCustomization.waiting_text); await state.update_data(custom_field="hint", custom_question=index); await callback.answer(); await callback.message.edit_text("💡 Отправь новую подсказку одним сообщением.")

@router.message(AdminCustomization.waiting_text)
async def custom_text_receive(message: Message, state: FSMContext):
    if not admin_allowed(message.from_user.id): await state.clear(); return
    text=(message.text or "").strip(); data=await state.get_data(); index=int(data.get("custom_question",-1)); field=data.get("custom_field")
    if field == "application_header":
        if not text:
            await message.answer("❌ Заголовок не может быть пустым.")
            return
        if len(text) > 80:
            await message.answer("❌ Заголовок слишком длинный. Максимум 80 символов.")
            return
        await set_custom_value("application:header", text)
        await state.clear()
        await message.answer(
            f"✅ <b>Заголовок сохранён</b>\n\nТекущий: <b>{html.escape(text)}</b>",
            reply_markup=application_header_style_keyboard(text),
        )
        return

    if not text or index < 0 or index >= len(QUESTIONS) or field not in {"title", "hint"}:
        await message.answer("❌ Некорректный текст.")
        return
    max_len = 200 if field == "title" else 800
    if len(text) > max_len:
        await message.answer(f"❌ Слишком длинный текст. Максимум {max_len} символов.")
        return
    await set_custom_value(f"question:{QUESTIONS[index]['key']}:{field}", text)
    await state.clear()
    await message.answer("✅ Сохранено.", reply_markup=custom_question_keyboard(index))


@router.callback_query(F.data.startswith("custom:emoji_for:"))
async def custom_emoji_for(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    index=int(callback.data.split(":")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        rows=await (await db.execute("SELECT * FROM custom_emojis ORDER BY id DESC")).fetchall()
    buttons=[[InlineKeyboardButton(text=f"{r['fallback']} {r['name']}", callback_data=f"emoji:set:{index}:{r['id']}")] for r in rows]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"custom:q:{index}")])
    await callback.answer(); await callback.message.edit_text("✨ <b>Выбери Premium Emoji</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("custom:preview_question:"))
async def custom_preview_question(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    index=int(callback.data.split(":")[-1]); q=QUESTIONS[index]; icon,title,hint=await build_question_visual(q)
    await callback.answer(); await callback.message.edit_text(f"👁 <b>ПРЕВЬЮ — {index+1}/{len(QUESTIONS)}</b>\n\n{icon} {title}\n\n💡 {hint}\n\nВыбери ответ ниже в реальной анкете.", reply_markup=custom_question_keyboard(index))

@router.callback_query(F.data == "custom:preview")
async def custom_preview(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    await callback.answer(); await callback.message.edit_text("👁 <b>ПРЕВЬЮ ВОПРОСОВ</b>\n\nВыбери вопрос:", reply_markup=customization_questions_keyboard("custom:preview_question"))

@router.callback_query(F.data == "custom:style")
async def custom_style(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    current=await get_custom_value("question:text_style",DEFAULT_TEXT_STYLE)
    await callback.answer(); await callback.message.edit_text("🔤 <b>СТИЛЬ ТЕКСТА</b>\n\nДоступны только стили форматирования Telegram. Произвольный шрифт боту выбрать нельзя.", reply_markup=text_style_keyboard(current))

@router.callback_query(F.data.startswith("custom:style:set:"))
async def custom_style_set(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    style=callback.data.split(":")[-1]
    if style not in TEXT_STYLES: await callback.answer("Недоступно", show_alert=True); return
    await set_custom_value("question:text_style",style); await callback.answer("Сохранено ✅"); await callback.message.edit_text(f"🔤 <b>Стиль:</b> {TEXT_STYLES[style]}", reply_markup=text_style_keyboard(style))

@router.callback_query(F.data.startswith("application:details:"))
async def application_details(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    try:
        application_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный номер.", show_alert=True)
        return

    application = await get_application(application_id)
    if application is None:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    await callback.answer()
    status_map = {
        "pending": "🕐 На рассмотрении",
        "accepted": "✅ Принята",
        "rejected": "❌ Отклонена",
    }
    status = status_map.get(application["status"], application["status"])
    reason = application["rejection_reason"] or "—"

    buttons = []
    if application["status"] == "pending":
        buttons.append([
            InlineKeyboardButton(
                text="✅ Принять",
                callback_data=f"decision:accept:{application_id}",
            ),
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"decision:reject:{application_id}",
            ),
        ])
    buttons.append([
        InlineKeyboardButton(text="🕘 История изменений", callback_data=f"application:history:{application_id}"),
    ])
    back_status = application["status"] if application["status"] in {"accepted", "rejected"} else "pending"
    buttons.append([
        InlineKeyboardButton(text="⬅️ К заявкам", callback_data=f"admin:{back_status}"),
        InlineKeyboardButton(text="🏠 Панель", callback_data="admin:home"),
    ])

    if callback.message:
        await callback.message.edit_text(
            f"📋 <b>ПОЛНАЯ ЗАЯВКА #{application_id}</b>\n\n"
            f"{application['application_text']}\n"
            "━━━━━━━━━━━━━━\n"
            f"Статус: <b>{html.escape(status)}</b>\n"
            f"Причина отказа: <b>{html.escape(reason)}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


@router.callback_query(F.data.startswith("application:history:"))
async def application_history(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    try:
        application_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный номер.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM application_history WHERE application_id = ? ORDER BY id DESC",
            (application_id,),
        )
        rows = await cur.fetchall()

    await callback.answer()
    if not callback.message:
        return

    if not rows:
        text = f"🕘 <b>История заявки #{application_id}</b>\n\nИзменений пока нет."
    else:
        lines = [f"🕘 <b>История заявки #{application_id}</b>", ""]
        for row in rows:
            who = html.escape(row["changed_by_name"] or str(row["changed_by"] or "система"))
            comment = html.escape(row["comment"] or "")
            lines.append(
                f"• {html.escape(row['old_status'] or '—')} → <b>{html.escape(row['new_status'])}</b>\n"
                f"  {html.escape(row['changed_at'])} — {who}"
            )
            if comment:
                lines.append(f"  💬 {comment}")
            lines.append("")
        text = "\n".join(lines)

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ К заявке", callback_data=f"application:details:{application_id}"),
            InlineKeyboardButton(text="🏠 Панель", callback_data="admin:home"),
        ]]),
    )


@router.message(Command("find"))
async def cmd_find(message: Message):
    if not is_staff_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Использование: <code>/find Player123</code> или <code>/find 42</code>")
        return
    query = parts[1].strip()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if query.isdigit():
            cur = await db.execute("SELECT * FROM applications WHERE id = ?", (int(query),))
        else:
            # Ищем по содержимому анкеты (в первую очередь Roblox-нику),
            # но не по Telegram username: контактные данные до принятия не раскрываем.
            cur = await db.execute(
                "SELECT * FROM applications "
                "WHERE answers_json LIKE ? OR application_text LIKE ? "
                "ORDER BY id DESC LIMIT 10",
                (f"%{query}%", f"%{query}%"),
            )
        rows = await cur.fetchall()
    if not rows:
        await message.answer("🔎 Ничего не найдено.")
        return
    lines = ["🔎 <b>Результаты поиска:</b>", ""]
    for row in rows:
        status_map = {
            "pending": "🕐 На рассмотрении",
            "accepted": "✅ Принята",
            "rejected": "❌ Отклонена",
        }
        lines.append(
            f"#{row['id']} — <b>{html.escape(status_map.get(row['status'], row['status']))}</b> — "
            f"🎮 {html.escape(application_roblox_name(row))}"
        )
    await message.answer("\n".join(lines))


def build_user_status_text(row) -> str:
    status_map = {
        "pending": "🕐 На рассмотрении",
        "accepted": "✅ Принята",
        "rejected": "❌ Отклонена",
    }
    text = (
        f"📋 <b>Заявка #{row['id']}</b>\n\n"
        f"Статус: <b>{html.escape(status_map.get(row['status'], row['status']))}</b>\n"
        f"Подана: <b>{html.escape(row['created_at'])}</b>"
    )
    if row["updated_at"]:
        text += f"\nОбновлена: <b>{html.escape(row['updated_at'])}</b>"
    reason = row["rejection_reason"]
    if reason:
        text += f"\nПричина: <b>{html.escape(reason)}</b>"
    comment = row["decision_comment"]
    if comment:
        text += f"\nКомментарий администрации: <b>{html.escape(comment)}</b>"
    return text


def user_home_text(row=None):
    if not row:
        return (
            "🎮 <b>ЛИЧНЫЙ КАБИНЕТ</b>\n\n"
            "📋 У тебя пока нет заявок.\n\n"
            "Нажми «📝 Подать анкету», чтобы отправить первую заявку."
        )
    if row["status"] == "pending":
        return (
            f"🎮 <b>ЛИЧНЫЙ КАБИНЕТ</b>\n\n"
            f"📋 Заявка <b>#{row['id']}</b>\n"
            "🕐 Статус: <b>На рассмотрении</b>\n\n"
            "Здесь можно открыть статус, анкету или историю."
        )
    if row["status"] == "accepted":
        return (
            f"🎮 <b>ЛИЧНЫЙ КАБИНЕТ</b>\n\n"
            f"📋 Заявка <b>#{row['id']}</b>\n"
            "✅ Статус: <b>Принята</b>\n\n"
            "Новую анкету после принятия подавать нельзя. Можно открыть статус и историю."
        )
    return (
        f"🎮 <b>ЛИЧНЫЙ КАБИНЕТ</b>\n\n"
        f"📋 Заявка <b>#{row['id']}</b>\n"
        "❌ Статус: <b>Отклонена</b>\n\n"
        "Можно посмотреть причину и подать новую анкету."
    )


@router.message(Command("status"))
async def cmd_status(message: Message):
    if admin_allowed(message.from_user.id):
        return

    row = await get_latest_user_application(message.from_user.id)
    if not row:
        await message.answer(
            user_home_text(),
            reply_markup=user_dashboard_keyboard(),
        )
        return

    await message.answer(
        build_user_status_text(row),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📄 Посмотреть анкету", callback_data=f"user:application:{row['id']}")],
            [InlineKeyboardButton(text="🗂 История заявок", callback_data="user:history")],
            *([[InlineKeyboardButton(text="📝 Подать анкету", callback_data="apply:start")]] if row["status"] == "rejected" else []),
            [InlineKeyboardButton(text="🏠 Кабинет", callback_data="user:home")],
        ]),
    )


@router.callback_query(F.data == "user:home")
async def user_home(callback: CallbackQuery):
    if admin_allowed(callback.from_user.id):
        await callback.answer("Эта кнопка доступна только пользователям.", show_alert=True)
        return
    row = await get_latest_user_application(callback.from_user.id)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            user_home_text(row),
            reply_markup=user_dashboard_keyboard(row["status"] if row else None),
        )


@router.callback_query(F.data == "user:status")
async def user_status(callback: CallbackQuery):
    if admin_allowed(callback.from_user.id):
        await callback.answer("Эта кнопка доступна только пользователям.", show_alert=True)
        return

    row = await get_latest_user_application(callback.from_user.id)
    await callback.answer()
    if callback.message:
        if not row:
            await callback.message.edit_text(
                "🎮 <b>ЛИЧНЫЙ КАБИНЕТ</b>\n\n"
                "📋 У тебя пока нет поданных заявок.",
                reply_markup=user_main_keyboard(),
            )
            return

        await callback.message.edit_text(
            build_user_status_text(row),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📄 Посмотреть анкету", callback_data=f"user:application:{row['id']}")],
                [InlineKeyboardButton(text="🗂 История заявок", callback_data="user:history")],
                *([[InlineKeyboardButton(text="📝 Подать анкету", callback_data="apply:start")]] if row["status"] == "rejected" else []),
                [InlineKeyboardButton(text="🏠 Кабинет", callback_data="user:home")],
            ]),
        )


@router.callback_query(F.data == "user:history")
async def user_history(callback: CallbackQuery):
    if admin_allowed(callback.from_user.id):
        await callback.answer("Эта кнопка доступна только пользователям.", show_alert=True)
        return

    rows = await get_user_applications(callback.from_user.id)
    if not rows:
        await callback.answer("История пока пуста.", show_alert=True)
        return

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "🗂 <b>ИСТОРИЯ ЗАЯВОК</b>\n\nВыбери заявку:",
            reply_markup=user_history_keyboard(rows),
        )


@router.callback_query(F.data.startswith("user:application:"))
async def user_application_details(callback: CallbackQuery):
    if admin_allowed(callback.from_user.id):
        await callback.answer("Эта кнопка доступна только пользователям.", show_alert=True)
        return

    try:
        application_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный номер заявки.", show_alert=True)
        return

    application = await get_application(application_id)
    if application is None or int(application["user_id"]) != callback.from_user.id:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    await callback.answer()
    if callback.message:
        text = build_user_status_text(application) + "\n\n━━━━━━━━━━━━━━\n" + application["application_text"]
        rows = [[
            InlineKeyboardButton(text="⬅️ История", callback_data="user:history"),
            InlineKeyboardButton(text="🏠 Кабинет", callback_data="user:home"),
        ]]
        if (
            application["status"] == "rejected"
            and await get_submission_block_status(callback.from_user.id) is None
        ):
            rows.insert(0, [InlineKeyboardButton(text="📝 Подать новую анкету", callback_data="apply:start")])
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


# =========================================================
# /START
# =========================================================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    if admin_allowed(message.from_user.id):
        await render_admin_panel(message)
        return

    row = await get_latest_user_application(message.from_user.id)
    await message.answer(
        user_home_text(row),
        reply_markup=user_dashboard_keyboard(row["status"] if row else None),
    )


# =========================================================
# /ID
# =========================================================

@router.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(
        "👤 <b>Твой Telegram ID:</b>\n"
        f"<code>{message.from_user.id}</code>\n\n"
        "💬 <b>ID этого чата:</b>\n"
        f"<code>{message.chat.id}</code>"
    )


# =========================================================
# /CANCEL
# =========================================================

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    row = await get_latest_user_application(message.from_user.id)
    await message.answer(
        "❌ Заполнение анкеты отменено.",
        reply_markup=user_dashboard_keyboard(row["status"] if row else None),
    )


# =========================================================
# НАЧАЛО АНКЕТЫ
# =========================================================

@router.callback_query(F.data == "apply:start")
async def start_application(callback: CallbackQuery, state: FSMContext):
    blocked_status = await get_submission_block_status(callback.from_user.id)
    if blocked_status == "accepted":
        await callback.answer(
            "Твоя заявка уже принята. Новую анкету подавать нельзя.",
            show_alert=True,
        )
        return
    if blocked_status == "pending":
        await callback.answer(
            "Твоя анкета уже находится на рассмотрении.",
            show_alert=True,
        )
        return

    await state.clear()
    await state.set_state(ApplicationForm.filling)
    await state.update_data(step=0, answers={}, custom_step=None)

    await callback.answer()

    if callback.message:
        await show_step(callback.message, state, 0, edit=True)


# =========================================================
# ОТМЕНА
# =========================================================

@router.callback_query(F.data == "form:cancel")
async def cancel_form(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Анкета отменена.")

    if callback.message:
        row = await get_latest_user_application(callback.from_user.id)
        await callback.message.edit_text(
            "❌ Заполнение анкеты отменено.",
            reply_markup=user_dashboard_keyboard(row["status"] if row else None),
        )


# =========================================================
# НАЗАД
# =========================================================

@router.callback_query(F.data == "form:back")
async def form_back(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != ApplicationForm.filling:
        await callback.answer("Сейчас возврат недоступен.", show_alert=True)
        return

    data = await state.get_data()
    step = int(data.get("step", 0))
    editing_step = data.get("editing_step")

    # При редактировании одного выбранного поля кнопка «Назад» должна вернуть
    # к экрану подтверждения, а не незаметно переключить пользователя на другое поле.
    if editing_step is not None:
        await state.set_state(ApplicationForm.confirming)
        await state.update_data(
            step=int(editing_step),
            custom_step=None,
            editing_step=None,
        )
        await callback.answer()
        if callback.message:
            await show_confirmation(callback.message, state, edit=True)
        return

    if step <= 0:
        await callback.answer("Это первый вопрос.")
        return

    previous_step = step - 1
    # Не удаляем сохранённые ответы: при возврате новое значение просто перезапишет старое.
    await state.update_data(step=previous_step, custom_step=None)

    await callback.answer()
    if callback.message:
        await show_step(callback.message, state, previous_step, edit=True)


# =========================================================
# ВЫБОР КНОПКОЙ
# =========================================================

@router.callback_query(F.data.startswith("form:choose:"))
async def form_choose(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != ApplicationForm.filling:
        await callback.answer("Анкета уже завершена.", show_alert=True)
        return

    parts = callback.data.split(":", 3)
    if len(parts) != 4:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    try:
        step = int(parts[2])
    except ValueError:
        await callback.answer("Некорректный шаг.", show_alert=True)
        return

    if step < 0 or step >= len(QUESTIONS):
        await callback.answer("Некорректный вопрос.", show_alert=True)
        return

    data = await state.get_data()
    if step != int(data.get("step", -1)):
        await callback.answer(
            "Этот вопрос уже неактуален. Используй текущую анкету.",
            show_alert=True,
        )
        return

    question = QUESTIONS[step]
    raw_value = parts[3]

    if question["type"] == "yes_no":
        if raw_value not in {"0", "1"}:
            await callback.answer("Некорректный ответ.", show_alert=True)
            return
        value = "Да" if raw_value == "0" else "Нет"
    elif question["type"] == "rating":
        if raw_value not in question["options"]:
            await callback.answer("Некорректная оценка.", show_alert=True)
            return
        value = raw_value
    else:
        try:
            index = int(raw_value)
            value = question["options"][index]
        except (ValueError, IndexError):
            await callback.answer("Некорректный вариант.", show_alert=True)
            return

    answers = dict(data.get("answers", {}))
    answers[question["key"]] = value
    editing_step = data.get("editing_step")
    next_step = step + 1

    await state.update_data(
        step=next_step,
        answers=answers,
        custom_step=None,
        editing_step=None,
    )

    await callback.answer()

    if editing_step is not None:
        await state.set_state(ApplicationForm.confirming)
        if callback.message:
            await show_confirmation(callback.message, state, edit=True)
    elif next_step >= len(QUESTIONS):
        await state.set_state(ApplicationForm.confirming)
        if callback.message:
            await show_confirmation(callback.message, state, edit=True)
    elif callback.message:
        await show_step(callback.message, state, next_step, edit=True)


# =========================================================
# СОБСТВЕННЫЙ ОТВЕТ
# =========================================================

@router.callback_query(F.data.startswith("form:custom:"))
async def form_custom(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != ApplicationForm.filling:
        await callback.answer("Анкета уже завершена.", show_alert=True)
        return

    try:
        step = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный шаг.", show_alert=True)
        return

    if step < 0 or step >= len(QUESTIONS):
        await callback.answer("Некорректный вопрос.", show_alert=True)
        return

    data = await state.get_data()
    if step != int(data.get("step", -1)):
        await callback.answer(
            "Этот вопрос уже неактуален. Используй текущую анкету.",
            show_alert=True,
        )
        return
    if QUESTIONS[step]["type"] not in {"level", "choice"}:
        await callback.answer("Для этого вопроса свой вариант недоступен.", show_alert=True)
        return

    await state.update_data(custom_step=step)
    await callback.answer()

    if callback.message:
        cfg = await get_question_config(QUESTIONS[step])
        await callback.message.edit_text(
            f"✍️ <b>{html.escape(cfg['title'])}</b>\n\n"
            "Напиши свой вариант одним сообщением.",
            reply_markup=text_question_keyboard(step),
        )


# =========================================================
# ТЕКСТОВЫЙ ОТВЕТ
# =========================================================

@router.message(ApplicationForm.filling)
async def form_text_answer(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Отправь ответ текстом.")
        return

    data = await state.get_data()
    step = int(data.get("step", 0))
    custom_step = data.get("custom_step")
    editing_step = data.get("editing_step")

    if custom_step is not None:
        step = int(custom_step)

    if step < 0 or step >= len(QUESTIONS):
        await message.answer("❌ Не удалось определить вопрос. Нажми /start.")
        await state.clear()
        return

    question = QUESTIONS[step]
    value = message.text.strip()

    if not value:
        await message.answer("❌ Ответ не может быть пустым.")
        return

    # Фиксированные вопросы нельзя обойти произвольным текстом.
    if custom_step is None and question["type"] in {"rating", "yes_no", "choice"}:
        await message.answer("❌ Выбери вариант кнопкой под текущим вопросом.")
        return

    max_len = ANSWER_MAX_LENGTH.get(question["key"], 250)
    if len(value) > max_len:
        await message.answer(f"❌ Ответ слишком длинный. Максимум {max_len} символов.")
        return

    if question["type"] == "level":
        if not value.isdigit() or int(value) <= 0:
            await message.answer("❌ Уровень должен быть положительным числом.")
            return

    answers = dict(data.get("answers", {}))
    answers[question["key"]] = value

    next_step = step + 1

    await state.update_data(
        step=next_step,
        answers=answers,
        custom_step=None,
        editing_step=None,
    )

    if editing_step is not None:
        await state.set_state(ApplicationForm.confirming)
        await show_confirmation(message, state)
    elif next_step >= len(QUESTIONS):
        await state.set_state(ApplicationForm.confirming)
        await show_confirmation(message, state)
    else:
        await show_step(message, state, next_step)


# =========================================================
# РЕДАКТИРОВАНИЕ ПЕРЕД ОТПРАВКОЙ
# =========================================================

@router.callback_query(F.data == "form:edit")
async def edit_form(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != ApplicationForm.confirming:
        await callback.answer("Сейчас редактирование недоступно.", show_alert=True)
        return

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "✏️ <b>Что изменить?</b>\n\nВыбери конкретный вопрос:",
            reply_markup=await edit_fields_keyboard(),
        )


@router.callback_query(F.data == "form:edit_back")
async def edit_back(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != ApplicationForm.confirming:
        await callback.answer("Сейчас возврат недоступен.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await show_confirmation(callback.message, state, edit=True)


@router.callback_query(F.data.startswith("form:edit_field:"))
async def edit_field(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != ApplicationForm.confirming:
        await callback.answer("Сейчас редактирование недоступно.", show_alert=True)
        return
    try:
        step = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный вопрос.", show_alert=True)
        return
    if step < 0 or step >= len(QUESTIONS):
        await callback.answer("Некорректный вопрос.", show_alert=True)
        return

    await state.set_state(ApplicationForm.filling)
    await state.update_data(editing_step=step, custom_step=None, step=step)
    await callback.answer()
    if callback.message:
        await show_step(callback.message, state, step, edit=True)


# =========================================================
# ОТПРАВКА АНКЕТЫ
# =========================================================

@router.callback_query(F.data == "form:submit")
async def submit_application(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != ApplicationForm.confirming:
        await callback.answer("Анкета уже отправлена или отменена.", show_alert=True)
        return

    user_id = callback.from_user.id
    data = await state.get_data()
    answers = dict(data.get("answers", {}))
    if len(answers) != len(QUESTIONS) or any(
        not str(answers.get(question["key"], "")).strip() for question in QUESTIONS
    ):
        await callback.answer("Не все вопросы заполнены.", show_alert=True)
        return

    application_text = await build_application_text(answers)
    username = callback.from_user.username
    full_name = callback.from_user.full_name
    now = datetime.now(timezone.utc).isoformat()

    # Проверка и INSERT выполняются в одной write-транзакции.
    # Это защищает от двух заявок при двойном клике по «Отправить».
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """
            SELECT status
            FROM applications
            WHERE user_id = ?
              AND status IN ('accepted', 'pending')
            ORDER BY CASE status WHEN 'accepted' THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        blocked = await cursor.fetchone()
        if blocked:
            await db.rollback()
            await state.clear()
            if blocked[0] == "accepted":
                await callback.answer(
                    "Твоя заявка уже принята. Новую анкету подавать нельзя.",
                    show_alert=True,
                )
            else:
                await callback.answer(
                    "У тебя уже есть анкета на рассмотрении.",
                    show_alert=True,
                )
            return

        cursor = await db.execute(
            """
            INSERT INTO applications (
                user_id, username, full_name, application_text, answers_json,
                status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                user_id,
                username,
                full_name,
                application_text,
                json.dumps(answers, ensure_ascii=False),
                now,
                now,
            ),
        )
        application_id = int(cursor.lastrowid)
        await db.commit()

    logger.info("application #%s created by user %s", application_id, user_id)

    # В STAFF показываем только полезную карточку для принятия решения.
    # Telegram username, ID и имя профиля здесь не публикуются.
    admin_text = build_staff_card(application_id, answers)

    try:
        staff_message = await callback.bot.send_message(
            ADMIN_CHAT_ID,
            admin_text,
            reply_markup=admin_keyboard(application_id),
        )
    except Exception as exc:
        await report_runtime_exception(
            callback.bot,
            exc,
            context=f"send_application_to_staff:{application_id}",
        )
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM applications WHERE id = ?", (application_id,))
            await db.commit()
        await callback.answer(
            "Не удалось отправить заявку администрации.",
            show_alert=True,
        )
        return

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE applications SET staff_message_id = ? WHERE id = ?",
                (staff_message.message_id, application_id),
            )
            await db.commit()
    except Exception as exc:
        await report_runtime_exception(
            callback.bot,
            exc,
            context=f"save_staff_message_id:{application_id}",
        )

    await state.clear()
    await callback.answer("✅ Анкета отправлена!")
    if callback.message:
        await callback.message.edit_text(
            f"✅ <b>Анкета #{application_id} отправлена!</b>\n\n"
            "Администрация рассмотрит её.\n"
            "Результат придёт тебе в этот чат.",
            reply_markup=user_dashboard_keyboard("pending"),
        )


# =========================================================
# ПРИНЯТИЕ КАНДИДАТА
# =========================================================

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
        await callback.answer(
            "Эта анкета уже была рассмотрена.",
            show_alert=True,
        )
        return

    await callback.answer("✅ Кандидат принят")

    user_id = int(application["user_id"])
    username = application["username"]
    full_name = application["full_name"]

    # Убираем кнопки и с текущего сообщения, и с исходной карточки в STAFF.
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("failed to remove decision keyboard for application #%s", application_id, exc_info=True)
    await disable_staff_application_keyboard(callback.bot, application)

    # Решение всегда публикуем именно в STAFF. Ошибка отправки не откатывает уже принятое решение.
    try:
        decision_message = await callback.bot.send_message(
            ADMIN_CHAT_ID,
            f"✅ <b>Заявка #{application_id} принята.</b>\n"
            "🔐 Контактные данные отправлены администратору в личные сообщения."
        )
        async with aiosqlite.connect(DB_PATH) as db:
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
        logger.warning("admin %s has not opened private chat; application #%s", callback.from_user.id, application_id)
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


# =========================================================
# ОТКЛОНЕНИЕ КАНДИДАТА
# =========================================================

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
        await callback.answer("Эта анкета уже рассмотрена.", show_alert=True)
        return

    await callback.answer()
    if callback.message:
        await callback.message.answer(
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
        await callback.answer("Эта анкета уже была рассмотрена.", show_alert=True)
        return

    await callback.answer("Заявка отклонена")

    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("failed to remove decision keyboard for application #%s", application_id, exc_info=True)
    await disable_staff_application_keyboard(callback.bot, application)

    try:
        decision_message = await callback.bot.send_message(
            ADMIN_CHAT_ID,
            f"❌ <b>Анкета #{application_id} отклонена.</b>\n"
            f"Причина: <b>{html.escape(reason)}</b>"
        )
        async with aiosqlite.connect(DB_PATH) as db:
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


# =========================================================
# ЗАПУСК БОТА
# =========================================================

async def main():

    bot = Bot(

        token=BOT_TOKEN,

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

    logger.info(
        "Evade Clan Bot запущен | db=%s | error_webhook=%s",
        DB_PATH,
        "enabled" if ERROR_WEBHOOK_URL else "disabled",
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


if __name__ == "__main__":

    asyncio.run(main())
