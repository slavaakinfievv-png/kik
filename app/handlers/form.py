"""Пошаговое заполнение, редактирование и отправка анкеты."""

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.types import Message
from datetime import datetime
from datetime import timezone
import aiosqlite
import html
import json

from app import config
from app.database import get_latest_user_application
from app.diagnostics import report_runtime_exception
from app.questions import ANSWER_MAX_LENGTH, QUESTIONS
from app.runtime import router
from app.states import ApplicationForm, _is_application_state, _state_matches
from app.ui import _validate_form_session, admin_keyboard, build_application_text, build_staff_card, edit_fields_keyboard, get_question_config, show_confirmation, show_step, text_question_keyboard, user_dashboard_keyboard

@router.callback_query(F.data.startswith("form:cancel:"))
async def cancel_form(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    current_state = await state.get_state()
    if not _is_application_state(current_state):
        await callback.answer("Эта анкета уже неактуальна.", show_alert=True)
        return
    if not await _validate_form_session(callback, state, parts[2]):
        return

    await state.clear()
    await callback.answer("Анкета отменена.")

    if callback.message:
        row = await get_latest_user_application(callback.from_user.id)
        await callback.message.edit_text(
            "❌ Заполнение анкеты отменено.",
            reply_markup=user_dashboard_keyboard(row["status"] if row else None),
        )


@router.callback_query(F.data.startswith("form:back:"))
async def form_back(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    if not _state_matches(await state.get_state(), ApplicationForm.filling):
        await callback.answer("Сейчас возврат недоступен.", show_alert=True)
        return
    if not await _validate_form_session(callback, state, parts[2]):
        return

    data = await state.get_data()
    step = int(data.get("step", 0))
    editing_step = data.get("editing_step")

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
    await state.update_data(step=previous_step, custom_step=None)

    await callback.answer()
    if callback.message:
        await show_step(callback.message, state, previous_step, edit=True)


@router.callback_query(F.data.startswith("form:choose:"))
async def form_choose(callback: CallbackQuery, state: FSMContext):
    if not _state_matches(await state.get_state(), ApplicationForm.filling):
        await callback.answer("Анкета уже завершена.", show_alert=True)
        return

    parts = (callback.data or "").split(":", 4)
    if len(parts) != 5:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    session_id = parts[2]
    if not await _validate_form_session(callback, state, session_id):
        return

    try:
        step = int(parts[3])
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
    raw_value = parts[4]

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


@router.callback_query(F.data.startswith("form:custom:"))
async def form_custom(callback: CallbackQuery, state: FSMContext):
    if not _state_matches(await state.get_state(), ApplicationForm.filling):
        await callback.answer("Анкета уже завершена.", show_alert=True)
        return

    parts = (callback.data or "").split(":", 3)
    if len(parts) != 4:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    if not await _validate_form_session(callback, state, parts[2]):
        return

    try:
        step = int(parts[3])
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
            reply_markup=text_question_keyboard(step, parts[2]),
        )


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


@router.callback_query(F.data.startswith("form:edit:"))
async def edit_form(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    if not _state_matches(await state.get_state(), ApplicationForm.confirming):
        await callback.answer("Сейчас редактирование недоступно.", show_alert=True)
        return
    if not await _validate_form_session(callback, state, parts[2]):
        return

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "✏️ <b>Что изменить?</b>\n\nВыбери конкретный вопрос:",
            reply_markup=await edit_fields_keyboard(parts[2]),
        )


@router.callback_query(F.data.startswith("form:edit_back:"))
async def edit_back(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    if not _state_matches(await state.get_state(), ApplicationForm.confirming):
        await callback.answer("Сейчас возврат недоступен.", show_alert=True)
        return
    if not await _validate_form_session(callback, state, parts[2]):
        return

    await callback.answer()
    if callback.message:
        await show_confirmation(callback.message, state, edit=True)


@router.callback_query(F.data.startswith("form:edit_field:"))
async def edit_field(callback: CallbackQuery, state: FSMContext):
    if not _state_matches(await state.get_state(), ApplicationForm.confirming):
        await callback.answer("Сейчас редактирование недоступно.", show_alert=True)
        return

    parts = (callback.data or "").split(":", 3)
    if len(parts) != 4:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    if not await _validate_form_session(callback, state, parts[2]):
        return
    try:
        step = int(parts[3])
    except ValueError:
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


@router.callback_query(F.data.startswith("form:submit:"))
async def submit_application(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    if not _state_matches(await state.get_state(), ApplicationForm.confirming):
        await callback.answer("Анкета уже отправлена или отменена.", show_alert=True)
        return
    if not await _validate_form_session(callback, state, parts[2]):
        return

    user_id = callback.from_user.id
    data = await state.get_data()
    answers = dict(data.get("answers", {}))
    if any(not str(answers.get(question["key"], "")).strip() for question in QUESTIONS):
        await callback.answer("Не все вопросы заполнены.", show_alert=True)
        return

    application_text = await build_application_text(answers)
    username = callback.from_user.username
    full_name = callback.from_user.full_name
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(config.DB_PATH) as db:
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
            if blocked[0] == "accepted":
                await state.clear()
                await callback.answer(
                    "Твоя заявка уже принята. Новую анкету подавать нельзя.",
                    show_alert=True,
                )
            else:
                # Не очищаем FSM: это может быть второй клик по «Отправить», пока
                # первый запрос ещё отправляет карточку в STAFF.
                await callback.answer(
                    "У тебя уже есть анкета на рассмотрении.",
                    show_alert=True,
                )
            return

        cursor = await db.execute(
            """
            INSERT INTO applications (
                user_id, username, full_name, application_text, answers_json,
                status, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                user_id,
                username,
                full_name,
                application_text,
                json.dumps(answers, ensure_ascii=False),
                now,
            ),
        )
        application_id = int(cursor.lastrowid)
        await db.commit()

    config.logger.info("application #%s created by user %s", application_id, user_id)
    admin_text = build_staff_card(application_id, answers)

    try:
        staff_message = await callback.bot.send_message(
            config.ADMIN_CHAT_ID,
            admin_text,
            reply_markup=admin_keyboard(application_id),
        )
    except Exception as exc:
        await report_runtime_exception(
            callback.bot,
            exc,
            context=f"send_application_to_staff:{application_id}",
        )
        async with aiosqlite.connect(config.DB_PATH) as db:
            await db.execute("DELETE FROM applications WHERE id = ?", (application_id,))
            await db.commit()
        await callback.answer(
            "Не удалось отправить заявку администрации. Попробуй отправить ещё раз.",
            show_alert=True,
        )
        return

    try:
        async with aiosqlite.connect(config.DB_PATH) as db:
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
