"""Environment configuration and logging setup."""

import logging
import os
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

# НАСТРОЙКИ
# =========================================================

load_dotenv()

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
)
logger = logging.getLogger(__name__)

LOG_FILE = os.getenv("LOG_FILE", "bot.log").strip()
if LOG_FILE:
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logging.getLogger().addHandler(file_handler)
    except OSError:
        logger.warning("Не удалось открыть файл логов %s", LOG_FILE, exc_info=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_CHAT_ID = int(
    os.getenv("ADMIN_CHAT_ID", "0")
)

ADMIN_IDS = {
    int(item.strip())
    for item in os.getenv(
        "ADMIN_IDS",
        ""
    ).split(",")
    if item.strip().isdigit()
}

DB_PATH = os.getenv(
    "DB_PATH",
    "applications.db"
).strip()

ERROR_WEBHOOK_URL = os.getenv("ERROR_WEBHOOK_URL", "").strip()
try:
    ERROR_NOTIFY_COOLDOWN_SECONDS = int(
        os.getenv("ERROR_NOTIFY_COOLDOWN_SECONDS", "300")
    )
except ValueError:
    ERROR_NOTIFY_COOLDOWN_SECONDS = 300
    logger.warning(
        "Некорректный ERROR_NOTIFY_COOLDOWN_SECONDS; используется 300 секунд"
    )


if not BOT_TOKEN:
    raise RuntimeError(
        "В файле .env не указан BOT_TOKEN"
    )


if not ADMIN_CHAT_ID:
    raise RuntimeError(
        "В файле .env не указан ADMIN_CHAT_ID"
    )

if not ADMIN_IDS:
    raise RuntimeError(
        "В файле .env не указан ADMIN_IDS. "
        "Укажи Telegram ID администраторов через запятую."
    )
