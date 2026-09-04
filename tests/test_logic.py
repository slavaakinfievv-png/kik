import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace

import aiosqlite

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_CHAT_ID", "-1001234567890")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("LOG_FILE", "")
os.environ.setdefault("ERROR_WEBHOOK_URL", "")

import bot
from app import config
from app.handlers import decisions as decision_handlers
from app.handlers import form as form_handlers


class BotLogicTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        config.DB_PATH = os.path.join(self.tmpdir.name, "applications.db")
        await bot.init_db()

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    async def _insert_pending(self, user_id=1):
        now = "2026-09-04T10:00:00+00:00"
        async with aiosqlite.connect(config.DB_PATH) as db:
            cursor = await db.execute(
                """
                INSERT INTO applications (
                    user_id, username, full_name, application_text,
                    answers_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (user_id, "tester", "Test User", "application", "{}", now),
            )
            await db.commit()
            return int(cursor.lastrowid)

    def test_form_callbacks_are_bound_to_session(self):
        value = bot._form_callback("choose", "cafebabe", 3, 1)
        self.assertEqual(value, "form:choose:cafebabe:3:1")
        self.assertLessEqual(len(value.encode("utf-8")), 64)

        first_question = bot.yes_no_keyboard(0, "cafebabe")
        callbacks = [
            button.callback_data
            for row in first_question.inline_keyboard
            for button in row
        ]
        self.assertNotIn("form:back:cafebabe", callbacks)
        self.assertIn("form:cancel:cafebabe", callbacks)

    def test_form_text_handler_does_not_consume_commands(self):
        self.assertFalse(form_handlers._is_form_input_message(SimpleNamespace(text="/cancel")))
        self.assertFalse(form_handlers._is_form_input_message(SimpleNamespace(text="/start")))
        self.assertFalse(form_handlers._is_form_input_message(SimpleNamespace(text="/status")))
        self.assertTrue(form_handlers._is_form_input_message(SimpleNamespace(text="обычный ответ")))
        self.assertTrue(form_handlers._is_form_input_message(SimpleNamespace(text=None)))

    def test_choice_index_must_be_inside_options(self):
        question = {"options": ["первый", "второй"]}
        self.assertEqual(form_handlers._choice_option(question, "0"), "первый")
        self.assertEqual(form_handlers._choice_option(question, "1"), "второй")
        self.assertIsNone(form_handlers._choice_option(question, "-1"))
        self.assertIsNone(form_handlers._choice_option(question, "2"))
        self.assertIsNone(form_handlers._choice_option(question, "invalid"))

    def test_private_admin_contact_is_escaped(self):
        application = {
            "user_id": 42,
            "username": "safe_user",
            "full_name": "<Admin & Candidate>",
        }
        text = decision_handlers._private_admin_contact_text(7, application)
        self.assertIn("Заявка #7", text)
        self.assertIn("&lt;Admin &amp; Candidate&gt;", text)
        self.assertIn("@safe_user", text)
        self.assertIn("<code>42</code>", text)

    async def test_staff_message_link_detects_deleted_application(self):
        application_id = await self._insert_pending()
        linked = await form_handlers._link_staff_message(application_id, 555)
        self.assertTrue(linked)

        async with aiosqlite.connect(config.DB_PATH) as db:
            await db.execute("DELETE FROM applications WHERE id = ?", (application_id,))
            await db.commit()

        linked_after_delete = await form_handlers._link_staff_message(application_id, 777)
        self.assertFalse(linked_after_delete)

    async def test_application_ids_are_not_reused_after_delete(self):
        application_id = await self._insert_pending()
        self.assertEqual(application_id, 1)

        async with aiosqlite.connect(config.DB_PATH) as db:
            await db.execute("DELETE FROM applications WHERE id = ?", (application_id,))
            await db.commit()

        last_existing, next_id = await bot._next_application_id()
        self.assertEqual(last_existing, 0)
        self.assertEqual(next_id, 2)

        second_id = await self._insert_pending(user_id=2)
        self.assertEqual(second_id, 2)

    async def test_decision_can_be_made_only_once(self):
        application_id = await self._insert_pending()

        results = await asyncio.gather(
            bot.make_decision(application_id, "accepted", 100, "Admin A"),
            bot.make_decision(
                application_id,
                "rejected",
                200,
                "Admin B",
                "Недостаточный уровень",
            ),
        )
        self.assertEqual(sum(bool(result) for result in results), 1)

        async with aiosqlite.connect(config.DB_PATH) as db:
            cursor = await db.execute(
                "SELECT status FROM applications WHERE id = ?",
                (application_id,),
            )
            status = (await cursor.fetchone())[0]
            self.assertIn(status, {"accepted", "rejected"})

            cursor = await db.execute(
                "SELECT COUNT(*) FROM application_history WHERE application_id = ?",
                (application_id,),
            )
            self.assertEqual((await cursor.fetchone())[0], 1)

    async def test_legacy_pending_updated_at_is_cleaned(self):
        now = "2026-09-04T10:00:00+00:00"
        async with aiosqlite.connect(config.DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO applications (
                    user_id, username, full_name, application_text,
                    answers_json, status, created_at, updated_at
                ) VALUES (1, 'tester', 'Test', 'application', '{}', 'pending', ?, ?)
                """,
                (now, now),
            )
            await db.commit()

        await bot.init_db()
        async with aiosqlite.connect(config.DB_PATH) as db:
            cursor = await db.execute("SELECT updated_at FROM applications WHERE id = 1")
            self.assertIsNone((await cursor.fetchone())[0])

    def test_staff_card_reflects_final_status(self):
        answers = {
            "roblox": "Player",
            "level": "100",
            "experience": "1 год",
            "activity": "Каждый день",
            "skill": "8",
            "teamwork": "9",
            "clans": "Нет",
            "rules": "Да",
            "reason": "Хочу играть с командой",
        }
        accepted = bot.build_staff_card(10, answers, status="accepted")
        rejected = bot.build_staff_card(
            11,
            answers,
            status="rejected",
            rejection_reason="Не подходит",
        )
        self.assertIn("Статус:</b> принята", accepted)
        self.assertNotIn("ожидает решения", accepted)
        self.assertIn("Статус:</b> отклонена", rejected)
        self.assertIn("Не подходит", rejected)


if __name__ == "__main__":
    unittest.main()
