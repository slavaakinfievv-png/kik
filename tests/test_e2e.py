import os
import tempfile
import unittest
from types import SimpleNamespace

import aiosqlite
from aiogram.enums import ChatType

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_CHAT_ID", "-1001234567890")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("LOG_FILE", "")
os.environ.setdefault("ERROR_WEBHOOK_URL", "")

from app import config
from app.database import get_application, init_db
from app.handlers import decisions as decision_handlers
from app.handlers import form as form_handlers
from app.handlers import user as user_handlers
from app.states import ApplicationForm, _state_matches


class FakeState:
    def __init__(self):
        self.state = None
        self.data = {}

    async def get_state(self):
        return self.state

    async def set_state(self, state):
        self.state = getattr(state, "state", state)

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)
        return dict(self.data)

    async def clear(self):
        self.state = None
        self.data = {}


class FakeBot:
    def __init__(self):
        self.next_message_id = 1000
        self.sent = []
        self.edited = []
        self.deleted = []

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self.next_message_id += 1
        item = {
            "chat_id": int(chat_id),
            "text": text,
            "reply_markup": reply_markup,
            "message_id": self.next_message_id,
        }
        self.sent.append(item)
        return SimpleNamespace(message_id=self.next_message_id)

    async def edit_message_text(self, *, chat_id, message_id, text, reply_markup=None, **kwargs):
        self.edited.append(
            {
                "chat_id": int(chat_id),
                "message_id": int(message_id),
                "text": text,
                "reply_markup": reply_markup,
            }
        )
        return True

    async def edit_message_reply_markup(self, *, chat_id, message_id, reply_markup=None, **kwargs):
        self.edited.append(
            {
                "chat_id": int(chat_id),
                "message_id": int(message_id),
                "text": None,
                "reply_markup": reply_markup,
            }
        )
        return True

    async def delete_message(self, chat_id, message_id, **kwargs):
        self.deleted.append((int(chat_id), int(message_id)))
        return True


class FakeMessage:
    _counter = 10

    def __init__(
        self,
        user,
        bot,
        *,
        text=None,
        chat_id=None,
        chat_type=ChatType.PRIVATE,
        message_id=None,
    ):
        if message_id is None:
            type(self)._counter += 1
            message_id = type(self)._counter
        self.message_id = int(message_id)
        self.from_user = user
        self.bot = bot
        self.text = text
        self.chat = SimpleNamespace(
            id=int(chat_id if chat_id is not None else user.id),
            type=chat_type,
        )
        self.answers = []
        self.edits = []
        self.deleted = False

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append({"text": text, "reply_markup": reply_markup})
        return SimpleNamespace(message_id=len(self.answers) + 2000)

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.edits.append({"text": text, "reply_markup": reply_markup})
        return True

    async def delete(self):
        self.deleted = True
        return True


class FakeCallback:
    def __init__(self, data, user, message, bot):
        self.data = data
        self.from_user = user
        self.message = message
        self.bot = bot
        self.answers = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append({"text": text, "show_alert": bool(show_alert)})
        return True


class BotEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = os.path.join(self.tmpdir.name, "e2e.db")
        config.ADMIN_IDS = {123456789, 987654321}
        config.ADMIN_CHAT_ID = -1001234567890
        await init_db()
        self.bot = FakeBot()
        self.user = SimpleNamespace(
            id=700001,
            username="candidate_user",
            full_name="Candidate <One>",
        )
        self.admin = SimpleNamespace(
            id=123456789,
            username="admin_one",
            full_name="Admin One",
        )

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    async def _start_form(self, user=None):
        user = user or self.user
        state = FakeState()
        message = FakeMessage(user, self.bot)
        callback = FakeCallback("apply:start", user, message, self.bot)
        await user_handlers.start_application(callback, state)
        self.assertTrue(_state_matches(await state.get_state(), ApplicationForm.filling))
        data = await state.get_data()
        self.assertEqual(data["step"], 0)
        self.assertTrue(data["session_id"])
        self.assertTrue(message.edits)
        return state, message, data["session_id"]

    async def _complete_and_submit(self, user=None):
        user = user or self.user
        state, message, session_id = await self._start_form(user)

        await form_handlers.form_text_answer(
            FakeMessage(user, self.bot, text="Игрок"), state
        )
        await form_handlers.form_text_answer(
            FakeMessage(user, self.bot, text="RobloxPlayer"), state
        )

        choices = [
            (2, "3"),  # level = 100
            (3, "1"),  # experience
            (4, "3"),  # UTC+3
            (5, "0"),  # activity
            (6, "8"),  # skill
            (7, "9"),  # teamwork
            (8, "1"),  # clans = Нет
            (9, "0"),  # reason
            (10, "0"),  # rules = Да
        ]
        for step, raw_value in choices:
            callback = FakeCallback(
                f"form:choose:{session_id}:{step}:{raw_value}",
                user,
                message,
                self.bot,
            )
            await form_handlers.form_choose(callback, state)

        self.assertTrue(_state_matches(await state.get_state(), ApplicationForm.confirming))
        data = await state.get_data()
        self.assertEqual(len(data["answers"]), 11)
        self.assertEqual(data["answers"]["roblox"], "RobloxPlayer")
        self.assertEqual(data["answers"]["level"], "100")
        self.assertEqual(data["answers"]["rules"], "Да")

        submit = FakeCallback(
            f"form:submit:{session_id}", user, message, self.bot
        )
        await form_handlers.submit_application(submit, state)
        self.assertIsNone(await state.get_state())
        self.assertTrue(
            any(item["chat_id"] == config.ADMIN_CHAT_ID for item in self.bot.sent)
        )

        async with aiosqlite.connect(config.DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT * FROM applications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                    (user.id,),
                )
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "pending")
        self.assertIsNotNone(row["staff_message_id"])
        return int(row["id"]), int(row["staff_message_id"]), message

    async def test_full_accept_flow_and_repeated_accept(self):
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
        await decision_handlers.accept_application(accept)

        application = await get_application(application_id)
        self.assertEqual(application["status"], "accepted")
        self.assertEqual(int(application["decided_by"]), self.admin.id)
        self.assertIsNotNone(application["decision_message_id"])

        async with aiosqlite.connect(config.DB_PATH) as db:
            count = (
                await (
                    await db.execute(
                        "SELECT COUNT(*) FROM application_history WHERE application_id = ?",
                        (application_id,),
                    )
                ).fetchone()
            )[0]
        self.assertEqual(count, 1)

        self.assertTrue(
            any(item["chat_id"] == self.admin.id for item in self.bot.sent),
            "accepting admin must receive candidate contact privately",
        )
        self.assertTrue(
            any(
                item["chat_id"] == self.user.id and "ПРИНЯТА" in item["text"]
                for item in self.bot.sent
            ),
            "candidate must receive acceptance notification",
        )
        self.assertTrue(
            any(
                item["message_id"] == staff_message_id
                and "принята" in (item["text"] or "")
                for item in self.bot.edited
            ),
            "original STAFF card must be finalized",
        )

        # Повторный клик старой кнопки не создаёт второе решение/историю.
        repeated = FakeCallback(
            f"decision:accept:{application_id}",
            self.admin,
            staff_message,
            self.bot,
        )
        await decision_handlers.accept_application(repeated)
        self.assertTrue(any(item["show_alert"] for item in repeated.answers))
        async with aiosqlite.connect(config.DB_PATH) as db:
            count = (
                await (
                    await db.execute(
                        "SELECT COUNT(*) FROM application_history WHERE application_id = ?",
                        (application_id,),
                    )
                ).fetchone()
            )[0]
        self.assertEqual(count, 1)

        # Принятый пользователь больше не может подать новую анкету.
        blocked_state = FakeState()
        blocked_message = FakeMessage(self.user, self.bot)
        blocked = FakeCallback("apply:start", self.user, blocked_message, self.bot)
        await user_handlers.start_application(blocked, blocked_state)
        self.assertIsNone(await blocked_state.get_state())
        self.assertTrue(
            any("уже принята" in (item["text"] or "") for item in blocked.answers)
        )

    async def test_full_reject_flow_allows_new_application(self):
        application_id, staff_message_id, _ = await self._complete_and_submit()
        staff_message = FakeMessage(
            self.admin,
            self.bot,
            chat_id=config.ADMIN_CHAT_ID,
            chat_type=ChatType.SUPERGROUP,
            message_id=staff_message_id,
        )

        reject = FakeCallback(
            f"decision:reject:{application_id}",
            self.admin,
            staff_message,
            self.bot,
        )
        await decision_handlers.reject_application(reject)
        self.assertTrue(staff_message.edits)
        markup = staff_message.edits[-1]["reply_markup"]
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn(f"reject_reason:{application_id}:low_level", callbacks)

        reason = FakeCallback(
            f"reject_reason:{application_id}:low_level",
            self.admin,
            staff_message,
            self.bot,
        )
        await decision_handlers.reject_reason(reason)
        application = await get_application(application_id)
        self.assertEqual(application["status"], "rejected")
        self.assertEqual(application["rejection_reason"], "Недостаточный уровень")
        self.assertTrue(
            any(
                item["chat_id"] == self.user.id and "ОТКЛОНЕНА" in item["text"]
                for item in self.bot.sent
            )
        )

        # После отказа пользователь может начать новую отдельную сессию.
        retry_state, _, retry_session = await self._start_form()
        self.assertTrue(retry_session)
        self.assertTrue(_state_matches(await retry_state.get_state(), ApplicationForm.filling))

    async def test_stale_button_cannot_change_current_question(self):
        state, message, session_id = await self._start_form()
        await form_handlers.form_text_answer(
            FakeMessage(self.user, self.bot, text="Игрок"), state
        )
        await form_handlers.form_text_answer(
            FakeMessage(self.user, self.bot, text="RobloxPlayer"), state
        )

        current = FakeCallback(
            f"form:choose:{session_id}:2:3", self.user, message, self.bot
        )
        await form_handlers.form_choose(current, state)
        before = await state.get_data()
        self.assertEqual(before["step"], 3)

        stale = FakeCallback(
            f"form:choose:{session_id}:2:0", self.user, message, self.bot
        )
        await form_handlers.form_choose(stale, state)
        after = await state.get_data()
        self.assertEqual(after["step"], 3)
        self.assertEqual(after["answers"]["level"], "100")
        self.assertTrue(any(item["show_alert"] for item in stale.answers))

    async def test_restart_makes_old_form_buttons_safely_inactive(self):
        state, message, session_id = await self._start_form()
        await form_handlers.form_text_answer(
            FakeMessage(self.user, self.bot, text="Игрок"), state
        )

        # MemoryStorage теряет незавершённый FSM после рестарта процесса.
        restarted_state = FakeState()
        old_button = FakeCallback(
            f"form:choose:{session_id}:1:0", self.user, message, self.bot
        )
        await form_handlers.form_choose(old_button, restarted_state)
        self.assertIsNone(await restarted_state.get_state())
        self.assertTrue(any(item["show_alert"] for item in old_button.answers))

        # Пользователь может безопасно начать заново через актуальную кнопку.
        restart = FakeCallback("apply:start", self.user, message, self.bot)
        await user_handlers.start_application(restart, restarted_state)
        self.assertTrue(_state_matches(await restarted_state.get_state(), ApplicationForm.filling))
        new_session = (await restarted_state.get_data())["session_id"]
        self.assertNotEqual(new_session, session_id)


if __name__ == "__main__":
    unittest.main()
