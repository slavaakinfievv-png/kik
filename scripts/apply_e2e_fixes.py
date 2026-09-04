from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"pattern not found in {path}: {old[:120]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all(path: str, old: str, new: str) -> int:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise RuntimeError(f"pattern not found in {path}: {old!r}")
    file_path.write_text(text.replace(old, new), encoding="utf-8")
    return count


back_count = replace_all(
    "app/ui.py",
    'callback_data=_form_callback("back", session_id),',
    'callback_data=_form_callback("back", session_id, step),',
)
if back_count < 4:
    raise RuntimeError(f"unexpectedly few back callbacks updated: {back_count}")

replace_once(
    "app/handlers/form.py",
    '''@router.callback_query(F.data.startswith("form:back:"))
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
''',
    '''@router.callback_query(F.data.startswith("form:back:"))
async def form_back(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":", 3)
    if len(parts) != 4:
        # Старые кнопки без номера шага безопасно считаем неактуальными.
        await callback.answer("Эта кнопка уже неактуальна.", show_alert=True)
        return
    if not _state_matches(await state.get_state(), ApplicationForm.filling):
        await callback.answer("Сейчас возврат недоступен.", show_alert=True)
        return
    if not await _validate_form_session(callback, state, parts[2]):
        return
    try:
        button_step = int(parts[3])
    except ValueError:
        await callback.answer("Некорректный шаг.", show_alert=True)
        return

    data = await state.get_data()
    step = int(data.get("step", 0))
    if button_step != step:
        await callback.answer(
            "Эта кнопка относится к предыдущему вопросу. Используй текущую анкету.",
            show_alert=True,
        )
        return
    editing_step = data.get("editing_step")
''',
)

replace_once(
    "app/handlers/decisions.py",
    '''    # Решение всегда публикуем именно в STAFF. Ошибка отправки не откатывает уже принятое решение.
    try:
        decision_message = await callback.bot.send_message(
            config.ADMIN_CHAT_ID,
            f"✅ <b>Заявка #{application_id} принята.</b>\\n"
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

    # После принятия данные получает только админ, который нажал «Принять».
    contact_sent = await _send_contact_to_accepting_admin(
        callback.bot,
        callback.from_user.id,
        application_id,
        application,
    )
    if not contact_sent and callback.message:
        await callback.message.answer(
            "⚠️ Не удалось отправить контакт в личные сообщения. "
            "Открой личный чат с ботом, нажми /start и затем используй "
            f"<code>/contact {application_id}</code>."
        )
''',
    '''    # Сначала пытаемся выдать контакт принявшему администратору, чтобы STAFF
    # показывал фактический результат доставки, а не обещание до сетевого вызова.
    contact_sent = await _send_contact_to_accepting_admin(
        callback.bot,
        callback.from_user.id,
        application_id,
        application,
    )
    if not contact_sent and callback.message:
        await callback.message.answer(
            "⚠️ Не удалось отправить контакт в личные сообщения. "
            "Открой личный чат с ботом, нажми /start и затем используй "
            f"<code>/contact {application_id}</code>."
        )

    # Решение публикуем в STAFF уже с фактическим состоянием доставки контакта.
    if contact_sent:
        contact_status = "🔐 Контактные данные отправлены администратору в личные сообщения."
    else:
        contact_status = (
            "⚠️ Контакт не доставлен в личные сообщения. "
            f"Принявшему администратору нужно использовать /contact {application_id}."
        )
    try:
        decision_message = await callback.bot.send_message(
            config.ADMIN_CHAT_ID,
            f"✅ <b>Заявка #{application_id} принята.</b>\\n{contact_status}",
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
''',
)

replace_once(
    "tests/test_e2e.py",
    '''import unittest
from types import SimpleNamespace
''',
    '''import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
''',
)

insert_before = '''    async def test_restart_makes_old_form_buttons_safely_inactive(self):
'''
new_tests = '''    async def test_stale_back_button_cannot_rewind_current_step(self):
        state, message, session_id = await self._start_form()
        await form_handlers.form_text_answer(
            FakeMessage(self.user, self.bot, text="Игрок"), state
        )
        await form_handlers.form_text_answer(
            FakeMessage(self.user, self.bot, text="RobloxPlayer"), state
        )

        choose_level = FakeCallback(
            f"form:choose:{session_id}:2:3", self.user, message, self.bot
        )
        await form_handlers.form_choose(choose_level, state)
        self.assertEqual((await state.get_data())["step"], 3)

        stale_back = FakeCallback(
            f"form:back:{session_id}:2", self.user, message, self.bot
        )
        await form_handlers.form_back(stale_back, state)
        self.assertEqual((await state.get_data())["step"], 3)
        self.assertTrue(any(item["show_alert"] for item in stale_back.answers))

        current_back = FakeCallback(
            f"form:back:{session_id}:3", self.user, message, self.bot
        )
        await form_handlers.form_back(current_back, state)
        self.assertEqual((await state.get_data())["step"], 2)

    async def test_staff_reports_contact_delivery_failure_truthfully(self):
        application_id, staff_message_id, _ = await self._complete_and_submit()
        staff_message = FakeMessage(
            self.admin,
            self.bot,
            chat_id=config.ADMIN_CHAT_ID,
            chat_type=ChatType.SUPERGROUP,
            message_id=staff_message_id,
        )
        accept = FakeCallback(
            f"decision:accept:{application_id}",
            self.admin,
            staff_message,
            self.bot,
        )

        with patch.object(
            decision_handlers,
            "_send_contact_to_accepting_admin",
            new=AsyncMock(return_value=False),
        ):
            await decision_handlers.accept_application(accept)

        staff_decisions = [
            item
            for item in self.bot.sent
            if item["chat_id"] == config.ADMIN_CHAT_ID
            and f"Заявка #{application_id} принята" in item["text"]
        ]
        self.assertEqual(len(staff_decisions), 1)
        self.assertIn("Контакт не доставлен", staff_decisions[0]["text"])
        self.assertIn(f"/contact {application_id}", staff_decisions[0]["text"])

'''
path = Path("tests/test_e2e.py")
text = path.read_text(encoding="utf-8")
if insert_before not in text:
    raise RuntimeError("test insertion point not found")
path.write_text(text.replace(insert_before, new_tests + insert_before, 1), encoding="utf-8")

for filename in ["app/ui.py", "app/handlers/form.py", "app/handlers/decisions.py", "tests/test_e2e.py"]:
    compile(Path(filename).read_text(encoding="utf-8"), filename, "exec")

print(f"updated {back_count} back callbacks and added E2E fixes")
