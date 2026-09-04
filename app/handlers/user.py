"""Личный кабинет пользователя, /start, статус и запуск анкеты."""

from aiogram import F
from aiogram.filters import Command
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.types import InlineKeyboardButton
from aiogram.types import InlineKeyboardMarkup
from aiogram.types import Message
import secrets

from app.database import admin_allowed, get_application, get_latest_user_application, get_submission_block_status, get_user_applications
from app.handlers.admin import render_admin_panel
from app.runtime import router
from app.states import ApplicationForm, _is_application_state
from app.ui import build_user_status_text, show_step, user_dashboard_keyboard, user_history_keyboard, user_home_text, user_main_keyboard

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


@router.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(
        "👤 <b>Твой Telegram ID:</b>\n"
        f"<code>{message.from_user.id}</code>\n\n"
        "💬 <b>ID этого чата:</b>\n"
        f"<code>{message.chat.id}</code>"
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()

    if admin_allowed(message.from_user.id):
        await state.clear()
        if current_state:
            await message.answer("❌ Текущее действие администратора отменено.")
        await render_admin_panel(message)
        return

    row = await get_latest_user_application(message.from_user.id)
    if not _is_application_state(current_state):
        await message.answer(
            "ℹ️ Сейчас нет активного заполнения анкеты.",
            reply_markup=user_dashboard_keyboard(row["status"] if row else None),
        )
        return

    await state.clear()
    await message.answer(
        "❌ Заполнение анкеты отменено.",
        reply_markup=user_dashboard_keyboard(row["status"] if row else None),
    )


@router.callback_query(F.data == "apply:start")
async def start_application(callback: CallbackQuery, state: FSMContext):
    if admin_allowed(callback.from_user.id):
        await callback.answer(
            "Администраторский аккаунт не может подавать анкету.",
            show_alert=True,
        )
        return

    current_state = await state.get_state()
    if _is_application_state(current_state):
        await callback.answer(
            "Анкета уже заполняется. Используй текущие кнопки или /cancel.",
            show_alert=True,
        )
        return

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

    session_id = secrets.token_hex(4)
    await state.clear()
    await state.set_state(ApplicationForm.filling)
    await state.update_data(
        step=0,
        answers={},
        custom_step=None,
        editing_step=None,
        session_id=session_id,
    )

    await callback.answer()
    if callback.message:
        await show_step(callback.message, state, 0, edit=True)
