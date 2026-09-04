"""Админ-панель, управление заявками и поиск."""

from aiogram import F
from aiogram.filters import Command
from aiogram.types import CallbackQuery
from aiogram.types import InlineKeyboardButton
from aiogram.types import InlineKeyboardMarkup
from aiogram.types import Message
import aiosqlite
import html

from app import config
from app.database import _next_application_id, _remove_staff_messages, admin_allowed, get_application
from app.runtime import router
from app.ui import admin_back_keyboard, admin_section_keyboard, admin_status_list_keyboard, application_roblox_name, customization_keyboard, pending_list_keyboard

def is_staff_admin(message: Message) -> bool:
    return admin_allowed(message.from_user.id)


@router.message(Command("apps"))
async def cmd_apps(message: Message):
    if not is_staff_admin(message):
        return

    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM applications")
        total = int((await cursor.fetchone())[0])
        cursor = await db.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'pending'"
        )
        pending = int((await cursor.fetchone())[0])

    last_id, next_id = await _next_application_id()
    await message.answer(
        "📊 <b>Статистика заявок</b>\n\n"
        f"📋 Всего заявок в базе: <b>{total}</b>\n"
        f"⏳ На рассмотрении: <b>{pending}</b>\n"
        f"🔢 Последний существующий номер: <b>{last_id}</b>\n"
        f"➡️ Следующий номер: <b>{next_id}</b>"
    )


@router.message(Command("clearapps"))
async def cmd_clearapps(message: Message):
    if not is_staff_admin(message):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "<code>/clearapps 5</code> — удалить 5 последних заявок и связанные сообщения\n"
            "<code>/clearapps all</code> — удалить все заявки и связанные сообщения\n\n"
            "🔐 Номера заявок намеренно не переиспользуются."
        )
        return

    value = parts[1].strip().lower()
    count: int | None
    if value == "all":
        count = None
    else:
        try:
            count = int(value)
        except ValueError:
            await message.answer("❌ Укажи целое число или <code>all</code>.")
            return
        if count <= 0:
            await message.answer("❌ Количество должно быть больше 0.")
            return

    # Снимок и удаление из БД делаем одной короткой write-транзакцией.
    # Сетевые вызовы Telegram выполняются уже после commit, чтобы не держать lock.
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        if count is None:
            cursor = await db.execute(
                "SELECT id, staff_message_id, decision_message_id "
                "FROM applications ORDER BY id DESC"
            )
        else:
            cursor = await db.execute(
                "SELECT id, staff_message_id, decision_message_id "
                "FROM applications ORDER BY id DESC LIMIT ?",
                (count,),
            )
        rows = await cursor.fetchall()
        ids = [int(row[0]) for row in rows]

        if ids:
            await db.executemany(
                "DELETE FROM application_history WHERE application_id = ?",
                [(application_id,) for application_id in ids],
            )
            await db.executemany(
                "DELETE FROM applications WHERE id = ?",
                [(application_id,) for application_id in ids],
            )
        await db.commit()

    _, deleted_messages, failed_messages = await _remove_staff_messages(
        message.bot,
        rows,
    )
    _, next_id = await _next_application_id()

    if count is None:
        await message.answer(
            "🗑 <b>Все заявки удалены.</b>\n\n"
            f"Удалено связанных сообщений: <b>{deleted_messages}</b>\n"
            f"Не удалось удалить сообщений: <b>{failed_messages}</b>\n"
            f"➡️ Следующая заявка получит номер <b>#{next_id}</b>.\n\n"
            "🔐 Старые номера не переиспользуются — это защищает от старых кнопок в Telegram."
        )
    else:
        await message.answer(
            f"🗑 Удалено заявок: <b>{len(rows)}</b>\n"
            f"Удалено связанных сообщений: <b>{deleted_messages}</b>\n"
            f"Не удалось удалить сообщений: <b>{failed_messages}</b>\n"
            f"➡️ Следующая заявка: <b>#{next_id}</b>."
        )


@router.message(Command("clearstaff"))
async def cmd_clearstaff(message: Message):
    if not is_staff_admin(message):
        return

    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, staff_message_id, decision_message_id FROM applications"
        )
        rows = await cursor.fetchall()

    success_ids, deleted, failed = await _remove_staff_messages(message.bot, rows)

    # После сетевых вызовов коротко синхронизируем только реально удалённые/отсутствующие ID.
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        for application_id, staff_message_id, decision_message_id in rows:
            if staff_message_id and int(staff_message_id) in success_ids:
                await db.execute(
                    "UPDATE applications SET staff_message_id = NULL WHERE id = ?",
                    (application_id,),
                )
            if decision_message_id and int(decision_message_id) in success_ids:
                await db.execute(
                    "UPDATE applications SET decision_message_id = NULL WHERE id = ?",
                    (application_id,),
                )
        await db.commit()

    try:
        await message.delete()
    except Exception:
        config.logger.debug("failed to delete /clearstaff command message", exc_info=True)

    await message.answer(
        f"🧹 Удалено сообщений бота из STAFF: <b>{deleted}</b>\n"
        f"⚠️ Не удалось удалить: <b>{failed}</b>\n"
        "Заявки и их номера не изменены. Неудалённые ID сохранены для повторной попытки."
    )


@router.message(Command("resetcounter"))
async def cmd_resetcounter(message: Message):
    if not is_staff_admin(message):
        return

    _, next_id = await _next_application_id()
    await message.answer(
        "🔐 <b>Сброс номеров отключён.</b>\n\n"
        "Повторное использование номера заявки небезопасно: в Telegram может остаться "
        "старое сообщение с кнопками этого номера.\n\n"
        f"Следующая заявка получит номер <b>#{next_id}</b>."
    )


async def render_admin_panel(message: Message):
    if not admin_allowed(message.from_user.id):
        return

    async with aiosqlite.connect(config.DB_PATH) as db:
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

    async with aiosqlite.connect(config.DB_PATH) as db:
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

    async with aiosqlite.connect(config.DB_PATH) as db:
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

    async with aiosqlite.connect(config.DB_PATH) as db:
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

    async with aiosqlite.connect(config.DB_PATH) as db:
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
            "<code>/clearapps all</code> — удалить все заявки, сохранив уникальность номеров\n"
            "<code>/clearstaff</code> — убрать сообщения бота из STAFF\n"
            "<code>/resetcounter</code> — показать, почему сброс номеров отключён",
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

    async with aiosqlite.connect(config.DB_PATH) as db:
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
    async with aiosqlite.connect(config.DB_PATH) as db:
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
