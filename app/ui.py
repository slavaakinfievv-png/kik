"""Клавиатуры, форматирование карточек и отображение шагов анкеты."""

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.types import InlineKeyboardButton
from aiogram.types import InlineKeyboardMarkup
from aiogram.types import Message
import html
import json

from app.database import get_custom_value
from app.questions import DEFAULT_TEXT_STYLE, QUESTIONS, TEXT_STYLES

def custom_emoji_html(emoji_id, fallback):
    safe_fallback = html.escape(str(fallback or "✨"))
    if not emoji_id:
        return safe_fallback
    return (
        f'<tg-emoji emoji-id="{html.escape(str(emoji_id), quote=True)}">'
        f'{safe_fallback}</tg-emoji>'
    )


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


def start_keyboard(status: str | None = None):
    return user_main_keyboard(status)


def _form_callback(action: str, session_id: str, *parts: object) -> str:
    suffix = ":".join(str(part) for part in parts)
    return f"form:{action}:{session_id}" + (f":{suffix}" if suffix else "")


def cancel_keyboard(session_id: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=_form_callback("cancel", session_id),
            )
        ]]
    )


def text_question_keyboard(step: int, session_id: str):
    rows = []
    if step > 0:
        rows.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=_form_callback("back", session_id),
            )
        )
    rows.append(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=_form_callback("cancel", session_id),
        )
    )
    return InlineKeyboardMarkup(inline_keyboard=[rows])


def choice_keyboard(step: int, options: list[str], session_id: str):
    rows = []
    for index, option in enumerate(options):
        rows.append([
            InlineKeyboardButton(
                text=option,
                callback_data=_form_callback("choose", session_id, step, index),
            )
        ])

    nav = []
    if step > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=_form_callback("back", session_id),
            )
        )
    nav.append(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=_form_callback("cancel", session_id),
        )
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def yes_no_keyboard(step: int, session_id: str):
    nav = []
    if step > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=_form_callback("back", session_id),
            )
        )
    nav.append(
        InlineKeyboardButton(
            text="🚫 Отмена",
            callback_data=_form_callback("cancel", session_id),
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да",
                    callback_data=_form_callback("choose", session_id, step, 0),
                ),
                InlineKeyboardButton(
                    text="❌ Нет",
                    callback_data=_form_callback("choose", session_id, step, 1),
                ),
            ],
            nav,
        ]
    )


def free_text_choice_keyboard(step: int, session_id: str):
    rows = [[
        InlineKeyboardButton(
            text="✍️ Ввести свой ответ",
            callback_data=_form_callback("custom", session_id, step),
        )
    ]]
    nav = []
    if step > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=_form_callback("back", session_id),
            )
        )
    nav.append(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=_form_callback("cancel", session_id),
        )
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirmation_keyboard(session_id: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Отправить",
                    callback_data=_form_callback("submit", session_id),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить",
                    callback_data=_form_callback("edit", session_id),
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=_form_callback("cancel", session_id),
                ),
            ],
        ]
    )


async def edit_fields_keyboard(session_id: str):
    rows = []
    for index, question in enumerate(QUESTIONS):
        cfg = await get_question_config(question)
        rows.append([
            InlineKeyboardButton(
                text=f"{index + 1}. {cfg['title'][:50]}",
                callback_data=_form_callback("edit_field", session_id, index),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К проверке анкеты",
            callback_data=_form_callback("edit_back", session_id),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _validate_form_session(
    callback: CallbackQuery,
    state: FSMContext,
    session_id: str,
) -> bool:
    data = await state.get_data()
    if not session_id or session_id != data.get("session_id"):
        await callback.answer(
            "Эта кнопка относится к старой анкете. Открой текущую анкету через /start.",
            show_alert=True,
        )
        return False
    return True


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


def build_staff_card(
    application_id: int,
    answers: dict[str, str],
    *,
    status: str = "pending",
    rejection_reason: str | None = None,
) -> str:
    """Короткая карточка заявки для STAFF без Telegram-контактов."""
    status_text = {
        "pending": "⏳ <b>Статус:</b> ожидает решения",
        "accepted": "✅ <b>Статус:</b> принята",
        "rejected": "❌ <b>Статус:</b> отклонена",
    }.get(status, f"📋 <b>Статус:</b> {html.escape(status)}")

    title = "🆕 <b>НОВАЯ ЗАЯВКА" if status == "pending" else "📋 <b>ЗАЯВКА"
    text = (
        f"{title} #{application_id}</b>\n\n"
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
        f"{status_text}"
    )
    if status == "rejected" and rejection_reason:
        text += f"\n💬 <b>Причина отказа:</b> {html.escape(rejection_reason)}"
    return text


async def show_step(message: Message, state: FSMContext, step: int, edit: bool = False):
    if step < 0 or step >= len(QUESTIONS):
        raise ValueError(f"Некорректный шаг анкеты: {step}")

    data = await state.get_data()
    session_id = str(data.get("session_id") or "")
    if not session_id:
        raise RuntimeError("В FSM отсутствует session_id анкеты")

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
        markup = text_question_keyboard(step, session_id)
    elif question["type"] == "level":
        text += "Выбери уровень:"
        markup = choice_keyboard(step, question["options"], session_id)
        markup.inline_keyboard.insert(
            -1,
            [InlineKeyboardButton(
                text="✍️ Ввести свой уровень",
                callback_data=_form_callback("custom", session_id, step),
            )],
        )
    elif question["type"] == "choice":
        text += "Выбери один вариант или введи свой ответ:"
        markup = choice_keyboard(step, question["options"], session_id)
        markup.inline_keyboard.insert(
            -1,
            [InlineKeyboardButton(
                text="✍️ Свой ответ",
                callback_data=_form_callback("custom", session_id, step),
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
                    callback_data=_form_callback("choose", session_id, step, value),
                )
                for value in row
            ]
            for row in rows
        ]
        nav = []
        if step > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=_form_callback("back", session_id),
                )
            )
        nav.append(
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=_form_callback("cancel", session_id),
            )
        )
        inline.append(nav)
        markup = InlineKeyboardMarkup(inline_keyboard=inline)
    elif question["type"] == "yes_no":
        text += "Выбери ответ:"
        markup = yes_no_keyboard(step, session_id)
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
    session_id = str(data.get("session_id") or "")
    if not session_id:
        raise RuntimeError("В FSM отсутствует session_id анкеты")

    header = await get_application_header()
    header_style = await get_custom_value("application:header_style", DEFAULT_TEXT_STYLE)
    if header_style not in TEXT_STYLES:
        header_style = DEFAULT_TEXT_STYLE
    styled_header = apply_text_style(header, header_style)

    lines = [
        f"🎮 <b>{styled_header}</b>",
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
    markup = confirmation_keyboard(session_id)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


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


def _decision_result_keyboard(application_id: int, status: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🕘 История изменений",
            callback_data=f"application:history:{application_id}",
        )],
        [
            InlineKeyboardButton(
                text="⬅️ К заявкам",
                callback_data=f"admin:{status}",
            ),
            InlineKeyboardButton(text="🏠 Панель", callback_data="admin:home"),
        ],
    ])
