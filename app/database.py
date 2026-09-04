"""Работа с SQLite: заявки, история, настройки и служебные операции."""

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from datetime import datetime
from datetime import timezone
import aiosqlite
import json

from app import config

async def get_custom_value(key, default=None):
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute("SELECT value FROM bot_customization WHERE key = ?", (key,))
        row = await cur.fetchone()
    return row[0] if row else default


async def set_custom_value(key, value):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "INSERT INTO bot_customization(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


async def init_db():

    async with aiosqlite.connect(
        config.DB_PATH
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

        # В ранней версии updated_at заполнялся в момент создания заявки,
        # поэтому пользователь видел одинаковые «Подана» и «Обновлена».
        # Для ожидающих решения заявок это не является реальным обновлением.
        await db.execute(
            """
            UPDATE applications
            SET updated_at = NULL
            WHERE status = 'pending'
              AND decided_at IS NULL
              AND updated_at = created_at
            """
        )

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_applications_user_id ON applications(user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_applications_status_id ON applications(status, id DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_application_id ON application_history(application_id, id DESC)"
        )
        await db.commit()


async def get_latest_user_application(user_id: int):
    async with aiosqlite.connect(config.DB_PATH) as db:
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
    async with aiosqlite.connect(config.DB_PATH) as db:
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
    async with aiosqlite.connect(config.DB_PATH) as db:
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
    async with aiosqlite.connect(config.DB_PATH) as db:
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


async def save_application(
    message: Message
):

    async with aiosqlite.connect(
        config.DB_PATH
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


async def get_application(application_id: int):
    async with aiosqlite.connect(config.DB_PATH) as db:
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


def _application_answers(application) -> dict[str, str] | None:
    raw = application["answers_json"]
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(key): str(value) for key, value in parsed.items()}


async def make_decision(
    application_id: int,
    status: str,
    admin_id: int,
    admin_name: str | None = None,
    rejection_reason: str | None = None,
    decision_comment: str | None = None,
):
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(config.DB_PATH) as db:
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

    config.logger.info(
        "application #%s: %s -> %s by admin %s (%s)",
        application_id, old_status, status, admin_id, admin_name or "unknown",
    )
    return True


def admin_allowed(
    user_id: int
):

    return user_id in config.ADMIN_IDS


async def _remove_staff_messages(bot: Bot, rows) -> tuple[set[int], int, int]:
    """Удаляет связанные STAFF-сообщения без удержания транзакции SQLite."""
    success_ids: set[int] = set()
    removed = 0
    failed = 0
    attempted: set[int] = set()

    for row in rows:
        staff_message_id = row[1]
        decision_message_id = row[2]
        for raw_message_id in (staff_message_id, decision_message_id):
            if not raw_message_id:
                continue
            message_id = int(raw_message_id)
            if message_id in attempted:
                continue
            attempted.add(message_id)
            try:
                await bot.delete_message(config.ADMIN_CHAT_ID, message_id)
                success_ids.add(message_id)
                removed += 1
            except TelegramBadRequest as exc:
                if "message to delete not found" in str(exc).lower():
                    # Сообщения уже нет, значит ссылку на него безопасно забыть.
                    success_ids.add(message_id)
                else:
                    failed += 1
                    config.logger.warning(
                        "failed to delete STAFF message %s",
                        message_id,
                        exc_info=True,
                    )
            except Exception:
                failed += 1
                config.logger.warning(
                    "failed to delete STAFF message %s",
                    message_id,
                    exc_info=True,
                )

    return success_ids, removed, failed


async def _next_application_id() -> tuple[int, int]:
    """Возвращает (последний существующий ID, следующий AUTOINCREMENT ID)."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute("SELECT MAX(id) FROM applications")
        last_existing = int((await cursor.fetchone())[0] or 0)
        cursor = await db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'applications'"
        )
        row = await cursor.fetchone()
        last_issued = int(row[0]) if row and row[0] is not None else last_existing
    return last_existing, last_issued + 1
