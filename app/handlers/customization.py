"""Кастомизация анкеты и Premium Emoji."""

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.types import InlineKeyboardButton
from aiogram.types import InlineKeyboardMarkup
from aiogram.types import Message
from datetime import datetime
from datetime import timezone
import aiosqlite
import html

from app import config
from app.database import admin_allowed, get_custom_value, set_custom_value
from app.handlers.admin import render_admin_panel
from app.questions import DEFAULT_TEXT_STYLE, QUESTIONS, TEXT_STYLES
from app.runtime import router
from app.states import AdminCustomization
from app.ui import application_header_style_keyboard, build_question_visual, custom_question_keyboard, get_application_header, get_question_config, text_style_keyboard

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


def _question_index_from_callback(data: str | None) -> int | None:
    try:
        index = int((data or "").rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None
    return index if 0 <= index < len(QUESTIONS) else None


async def _configured_questions_keyboard(prefix: str = "custom:q"):
    rows = []
    for index, question in enumerate(QUESTIONS):
        cfg = await get_question_config(question)
        rows.append([
            InlineKeyboardButton(
                text=f"{index + 1}. {cfg['title'][:50]}",
                callback_data=f"{prefix}:{index}",
            )
        ])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _handle_admin_state_command(message: Message, state: FSMContext) -> bool:
    text = (message.text or "").strip()
    if not text.startswith("/"):
        return False

    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
    if command in {"/cancel", "/start"}:
        await state.clear()
        await message.answer("❌ Редактирование отменено.")
        await render_admin_panel(message)
    else:
        await message.answer(
            "ℹ️ Сейчас открыт режим редактирования. "
            "Заверши его или используй /cancel."
        )
    return True


@router.callback_query(F.data == "custom:emoji")
async def custom_emoji_menu(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "✨ <b>PREMIUM EMOJI</b>\n\n"
            "Добавляй Premium Emoji прямо сообщением и назначай его на вопросы.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить", callback_data="emoji:add")],
                [InlineKeyboardButton(text="📋 Мои emoji", callback_data="emoji:list")],
                [InlineKeyboardButton(text="🎯 Назначить на вопрос", callback_data="emoji:assign")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:custom")],
            ]),
        )


@router.callback_query(F.data == "emoji:add")
async def emoji_add(callback: CallbackQuery, state: FSMContext):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state(AdminCustomization.waiting_emoji)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "✨ <b>Отправь Premium Emoji</b>\n\n"
            "Отправь одним сообщением именно кастомный emoji из Telegram.\n"
            "Для выхода используй /cancel."
        )


@router.message(AdminCustomization.waiting_emoji)
async def emoji_receive(message: Message, state: FSMContext):
    if not admin_allowed(message.from_user.id):
        await state.clear()
        return
    if await _handle_admin_state_command(message, state):
        return

    entity = next(
        (
            entity
            for entity in (message.entities or [])
            if entity.type == "custom_emoji" and entity.custom_emoji_id
        ),
        None,
    )
    if entity is None or not message.text:
        await message.answer(
            "❌ Кастомный emoji не найден. Отправь Premium Emoji ещё раз "
            "или используй /cancel."
        )
        return

    # Для типичного сообщения из одного Premium Emoji offset=0. Если Telegram
    # прислал сложную строку, не пытаемся угадывать UTF-16 offset и берём
    # безопасный визуальный fallback.
    fallback = message.text if len(message.text) <= 4 else "✨"
    name = f"emoji_{entity.custom_emoji_id[-6:]}"
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO custom_emojis(name, emoji_id, fallback, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                name,
                entity.custom_emoji_id,
                fallback,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
        inserted = cursor.rowcount == 1

    await state.clear()
    await message.answer(
        ("✅ <b>Emoji сохранён</b>" if inserted else "ℹ️ <b>Такой Emoji уже был сохранён</b>")
        + f"\n\n{html.escape(fallback)}\nID: <code>{html.escape(entity.custom_emoji_id)}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ В Premium Emoji", callback_data="custom:emoji")
        ]]),
    )


@router.callback_query(F.data == "emoji:list")
async def emoji_list(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM custom_emojis ORDER BY id DESC"
        )).fetchall()

    text = "📋 <b>МОИ PREMIUM EMOJI</b>\n\n"
    if rows:
        text += "\n".join(
            f"{html.escape(row['fallback'])} <b>{html.escape(row['name'])}</b> — "
            f"<code>{html.escape(row['emoji_id'])}</code>"
            for row in rows
        )
    else:
        text += "Пока пусто."

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data="custom:emoji")
            ]]),
        )


@router.callback_query(F.data == "emoji:assign")
async def emoji_assign(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "🎯 <b>Выбери вопрос:</b>",
            reply_markup=await _configured_questions_keyboard("emoji:q"),
        )


@router.callback_query(F.data.startswith("emoji:q:"))
async def emoji_question(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    index = _question_index_from_callback(callback.data)
    if index is None:
        await callback.answer("Некорректный вопрос.", show_alert=True)
        return

    question = QUESTIONS[index]
    cfg = await get_question_config(question)
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM custom_emojis ORDER BY id DESC"
        )).fetchall()

    buttons = [
        [InlineKeyboardButton(
            text=f"{row['fallback']} {row['name']}",
            callback_data=f"emoji:set:{index}:{row['id']}",
        )]
        for row in rows
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="emoji:assign")])

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"🎯 <b>{html.escape(cfg['title'])}</b>\n\n"
            + ("Выбери emoji:" if rows else "Сначала добавь хотя бы один Premium Emoji."),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


@router.callback_query(F.data.startswith("emoji:set:"))
async def emoji_set(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    parts = (callback.data or "").split(":", 3)
    if len(parts) != 4:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    try:
        index = int(parts[2])
        emoji_db_id = int(parts[3])
    except ValueError:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    if index < 0 or index >= len(QUESTIONS):
        await callback.answer("Некорректный вопрос.", show_alert=True)
        return

    async with aiosqlite.connect(config.DB_PATH) as db:
        row = await (await db.execute(
            "SELECT emoji_id FROM custom_emojis WHERE id = ?",
            (emoji_db_id,),
        )).fetchone()
    if not row:
        await callback.answer("Emoji не найден.", show_alert=True)
        return

    await set_custom_value(f"question:{QUESTIONS[index]['key']}:emoji_id", row[0])
    await callback.answer("Назначен ✅")
    if callback.message:
        await callback.message.edit_text(
            "✅ Emoji назначен.",
            reply_markup=custom_question_keyboard(index),
        )


@router.callback_query(F.data == "custom:texts")
async def custom_texts(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "📝 <b>ТЕКСТЫ ВОПРОСОВ</b>\n\nВыбери вопрос:",
            reply_markup=await _configured_questions_keyboard(),
        )


@router.callback_query(F.data.startswith("custom:q:"))
async def custom_q(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    index = _question_index_from_callback(callback.data)
    if index is None:
        await callback.answer("Некорректный вопрос.", show_alert=True)
        return
    question = QUESTIONS[index]
    cfg = await get_question_config(question)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"📝 <b>Вопрос {index + 1}</b>\n\n"
            f"<b>Заголовок:</b> {html.escape(cfg['title'])}\n"
            f"<b>Подсказка:</b> {html.escape(cfg['hint'])}",
            reply_markup=custom_question_keyboard(index),
        )


@router.callback_query(F.data.startswith("custom:title:"))
async def custom_title(callback: CallbackQuery, state: FSMContext):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    index = _question_index_from_callback(callback.data)
    if index is None:
        await callback.answer("Некорректный вопрос.", show_alert=True)
        return
    await state.set_state(AdminCustomization.waiting_text)
    await state.update_data(custom_field="title", custom_question=index)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "✏️ Отправь новый заголовок одним сообщением.\n"
            "Для выхода используй /cancel."
        )


@router.callback_query(F.data.startswith("custom:hint:"))
async def custom_hint(callback: CallbackQuery, state: FSMContext):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    index = _question_index_from_callback(callback.data)
    if index is None:
        await callback.answer("Некорректный вопрос.", show_alert=True)
        return
    await state.set_state(AdminCustomization.waiting_text)
    await state.update_data(custom_field="hint", custom_question=index)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "💡 Отправь новую подсказку одним сообщением.\n"
            "Для выхода используй /cancel."
        )


@router.message(AdminCustomization.waiting_text)
async def custom_text_receive(message: Message, state: FSMContext):
    if not admin_allowed(message.from_user.id):
        await state.clear()
        return
    if await _handle_admin_state_command(message, state):
        return

    text = (message.text or "").strip()
    data = await state.get_data()
    field = data.get("custom_field")

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

    try:
        index = int(data.get("custom_question", -1))
    except (TypeError, ValueError):
        index = -1
    if not text or index < 0 or index >= len(QUESTIONS) or field not in {"title", "hint"}:
        await message.answer("❌ Некорректный текст. Используй /cancel и попробуй снова.")
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
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    index = _question_index_from_callback(callback.data)
    if index is None:
        await callback.answer("Некорректный вопрос.", show_alert=True)
        return

    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM custom_emojis ORDER BY id DESC"
        )).fetchall()
    buttons = [
        [InlineKeyboardButton(
            text=f"{row['fallback']} {row['name']}",
            callback_data=f"emoji:set:{index}:{row['id']}",
        )]
        for row in rows
    ]
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data=f"custom:q:{index}")
    ])
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "✨ <b>Выбери Premium Emoji</b>\n\n"
            + ("" if rows else "Сначала добавь хотя бы один emoji."),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


@router.callback_query(F.data.startswith("custom:preview_question:"))
async def custom_preview_question(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    index = _question_index_from_callback(callback.data)
    if index is None:
        await callback.answer("Некорректный вопрос.", show_alert=True)
        return
    question = QUESTIONS[index]
    icon, title, hint = await build_question_visual(question)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"👁 <b>ПРЕВЬЮ — {index + 1}/{len(QUESTIONS)}</b>\n\n"
            f"{icon} {title}\n\n💡 {hint}\n\n"
            "Выбери ответ ниже в реальной анкете.",
            reply_markup=custom_question_keyboard(index),
        )


@router.callback_query(F.data == "custom:preview")
async def custom_preview(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "👁 <b>ПРЕВЬЮ ВОПРОСОВ</b>\n\nВыбери вопрос:",
            reply_markup=await _configured_questions_keyboard("custom:preview_question"),
        )


@router.callback_query(F.data == "custom:style")
async def custom_style(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    current = await get_custom_value("question:text_style", DEFAULT_TEXT_STYLE)
    if current not in TEXT_STYLES:
        current = DEFAULT_TEXT_STYLE
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "🔤 <b>СТИЛЬ ТЕКСТА</b>\n\n"
            "Доступны только стили форматирования Telegram. "
            "Произвольный шрифт боту выбрать нельзя.",
            reply_markup=text_style_keyboard(current),
        )


@router.callback_query(F.data.startswith("custom:style:set:"))
async def custom_style_set(callback: CallbackQuery):
    if not admin_allowed(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    style = (callback.data or "").split(":")[-1]
    if style not in TEXT_STYLES:
        await callback.answer("Недоступно.", show_alert=True)
        return
    await set_custom_value("question:text_style", style)
    await callback.answer("Сохранено ✅")
    if callback.message:
        await callback.message.edit_text(
            f"🔤 <b>Стиль:</b> {html.escape(TEXT_STYLES[style])}",
            reply_markup=text_style_keyboard(style),
        )
