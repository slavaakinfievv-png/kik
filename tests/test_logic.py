import asyncio
import os
import tempfile
import unittest

import aiosqlite

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_CHAT_ID", "-1001234567890")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("LOG_FILE", "")
os.environ.setdefault("ERROR_WEBHOOK_URL", "")

import bot


class BotLogicTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        bot.DB_PATH = os.path.join(self.tmpdir.name, "applications.db")
        await bot.init_db()

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    async def _insert_pending(self, user_id=1):
        now = "2026-09-04T10:00:00+00:00"
        async with aiosqlite.connect(bot.DB_PATH) as db:
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

    async def test_application_ids_are_not_reused_after_delete(self):
        application_id = await self._insert_pending()
        self.assertEqual(application_id, 1)

        async with aiosqlite.connect(bot.DB_PATH) as db:
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

        async with aiosqlite.connect(bot.DB_PATH) as db:
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
        async with aiosqlite.connect(bot.DB_PATH) as db:
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
        async with aiosqlite.connect(bot.DB_PATH) as db:
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
