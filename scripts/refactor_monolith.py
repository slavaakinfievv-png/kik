from __future__ import annotations
import ast
from pathlib import Path

SRC = Path('bot.py')
OUT = Path('.')
source = SRC.read_text(encoding='utf-8')
lines = source.splitlines(keepends=True)
mod = ast.parse(source)

nodes: dict[str, ast.AST] = {}
for node in mod.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        nodes[node.name] = node
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                nodes[target.id] = node
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        nodes[node.target.id] = node

config_names = {
    'LOG_FORMAT', 'logger', 'LOG_FILE', 'BOT_TOKEN', 'ADMIN_CHAT_ID', 'ADMIN_IDS',
    'DB_PATH', 'ERROR_WEBHOOK_URL', 'ERROR_NOTIFY_COOLDOWN_SECONDS',
}

groups: dict[str, set[str]] = {
    'runtime': {'router'},
    'states': {'ApplicationForm', 'AdminCustomization', '_state_matches', '_is_application_state'},
    'questions': {'QUESTIONS', 'ANSWER_MAX_LENGTH', 'DEFAULT_TEXT_STYLE', 'TEXT_STYLES', 'REJECTION_REASONS'},
    'diagnostics': {'_error_last_notified', '_error_update_context', '_build_error_payload', '_post_error_webhook', '_send_error_webhook', 'report_runtime_exception', 'global_error_handler'},
    'database': {'get_custom_value', 'set_custom_value', 'init_db', 'get_latest_user_application', 'get_user_applications', 'has_pending_application', 'get_submission_block_status', 'save_application', 'get_application', '_application_answers', 'make_decision', 'admin_allowed', '_remove_staff_messages', '_next_application_id'},
    'services': {'refresh_staff_application_message', 'disable_staff_application_keyboard'},
    'ui': {'custom_emoji_html', 'apply_text_style', 'get_question_config', 'build_question_visual', 'get_application_header', 'application_header_style_keyboard', 'customization_keyboard', 'customization_questions_keyboard', 'custom_question_keyboard', 'text_style_keyboard', 'user_main_keyboard', 'start_keyboard', '_form_callback', 'cancel_keyboard', 'text_question_keyboard', 'choice_keyboard', 'yes_no_keyboard', 'free_text_choice_keyboard', 'confirmation_keyboard', 'edit_fields_keyboard', '_validate_form_session', 'admin_keyboard', 'contact_keyboard', 'build_application_text', 'build_staff_card', 'show_step', 'show_confirmation', 'admin_section_keyboard', 'user_history_keyboard', 'user_dashboard_keyboard', 'application_roblox_name', 'admin_status_list_keyboard', 'admin_panel_keyboard', 'admin_back_keyboard', 'pending_list_keyboard', 'rejection_keyboard', 'build_user_status_text', 'user_home_text', '_decision_result_keyboard'},
    'handlers_admin': set(),
    'handlers_customization': set(),
    'handlers_user': set(),
    'handlers_form': set(),
    'handlers_decisions': set(),
    'main': {'main'},
}

assigned = set().union(*groups.values()) | config_names
for name, node in nodes.items():
    if name in assigned:
        continue
    lineno = getattr(node, 'lineno', 0)
    if 1320 <= lineno < 1887 or 2419 <= lineno < 2565:
        groups['handlers_admin'].add(name)
    elif 1887 <= lineno < 2419:
        groups['handlers_customization'].add(name)
    elif 2565 <= lineno < 2862:
        groups['handlers_user'].add(name)
    elif 2862 <= lineno < 3329:
        groups['handlers_form'].add(name)
    elif 3329 <= lineno < 3631:
        groups['handlers_decisions'].add(name)
    elif lineno >= 3631:
        groups['main'].add(name)
    else:
        raise RuntimeError(f'Unassigned top-level name: {name} line {lineno}')

seen = set()
for group, names in groups.items():
    overlap = seen & names
    if overlap:
        raise RuntimeError(f'duplicate assignments: {overlap}')
    seen |= names
missing = set(nodes) - seen - config_names
if missing:
    raise RuntimeError(f'missing assignments: {sorted(missing)}')

module_path = {
    'runtime': 'app.runtime', 'states': 'app.states', 'questions': 'app.questions',
    'diagnostics': 'app.diagnostics', 'database': 'app.database', 'services': 'app.services',
    'ui': 'app.ui', 'handlers_admin': 'app.handlers.admin',
    'handlers_customization': 'app.handlers.customization', 'handlers_user': 'app.handlers.user',
    'handlers_form': 'app.handlers.form', 'handlers_decisions': 'app.handlers.decisions',
    'main': 'app.main',
}
name_group = {name: group for group, names in groups.items() for name in names}
for name in config_names:
    name_group[name] = 'config'


def node_text(node: ast.AST) -> str:
    start = getattr(node, 'lineno')
    decorators = getattr(node, 'decorator_list', [])
    if decorators:
        start = min(start, *(d.lineno for d in decorators))
    end = getattr(node, 'end_lineno')
    return ''.join(lines[start - 1:end]).rstrip() + '\n'


def refs(node: ast.AST) -> set[str]:
    result = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            result.add(child.id)
    return result

IMPORT_MAP = {
    'asyncio': 'import asyncio', 'hashlib': 'import hashlib', 'html': 'import html',
    'json': 'import json', 'logging': 'import logging', 'os': 'import os',
    'secrets': 'import secrets', 'traceback': 'import traceback', 'urllib': 'import urllib.request',
    'datetime': 'from datetime import datetime', 'timezone': 'from datetime import timezone',
    'monotonic': 'from time import monotonic', 'aiosqlite': 'import aiosqlite',
    'Bot': 'from aiogram import Bot', 'Dispatcher': 'from aiogram import Dispatcher',
    'F': 'from aiogram import F', 'DefaultBotProperties': 'from aiogram.client.default import DefaultBotProperties',
    'ParseMode': 'from aiogram.enums import ParseMode',
    'TelegramBadRequest': 'from aiogram.exceptions import TelegramBadRequest',
    'TelegramForbiddenError': 'from aiogram.exceptions import TelegramForbiddenError',
    'Command': 'from aiogram.filters import Command', 'CommandStart': 'from aiogram.filters import CommandStart',
    'FSMContext': 'from aiogram.fsm.context import FSMContext', 'State': 'from aiogram.fsm.state import State',
    'StatesGroup': 'from aiogram.fsm.state import StatesGroup', 'CallbackQuery': 'from aiogram.types import CallbackQuery',
    'ErrorEvent': 'from aiogram.types import ErrorEvent', 'InlineKeyboardButton': 'from aiogram.types import InlineKeyboardButton',
    'InlineKeyboardMarkup': 'from aiogram.types import InlineKeyboardMarkup', 'Message': 'from aiogram.types import Message',
}

DOCS = {
    'runtime': 'Общий Router. Все handler-модули регистрируются на одном экземпляре.',
    'states': 'FSM-состояния анкеты и вспомогательные проверки состояний.',
    'questions': 'Статические данные анкеты, лимиты и справочники.',
    'diagnostics': 'Глобальная диагностика, traceback и уведомления об ошибках.',
    'database': 'Работа с SQLite: заявки, история, настройки и служебные операции.',
    'services': 'Сервисные операции, связывающие БД и Telegram-сообщения.',
    'ui': 'Клавиатуры, форматирование карточек и отображение шагов анкеты.',
    'handlers_admin': 'Админ-панель, управление заявками и поиск.',
    'handlers_customization': 'Кастомизация анкеты и Premium Emoji.',
    'handlers_user': 'Личный кабинет пользователя, /start, статус и запуск анкеты.',
    'handlers_form': 'Пошаговое заполнение, редактирование и отправка анкеты.',
    'handlers_decisions': 'Принятие и отклонение заявок администраторами.',
    'main': 'Сборка приложения и запуск polling.',
}

OUT.mkdir(parents=True, exist_ok=True)
(OUT / 'app' / 'handlers').mkdir(parents=True, exist_ok=True)
(OUT / 'app' / '__init__.py').write_text('"""Evade Clan bot package."""\n', encoding='utf-8')
(OUT / 'app' / 'handlers' / '__init__.py').write_text('"""Telegram update handlers."""\n', encoding='utf-8')

config_text = '''"""Environment configuration and logging setup."""\n\nimport logging\nimport os\nfrom logging.handlers import RotatingFileHandler\n\nfrom dotenv import load_dotenv\n\n''' + ''.join(lines[34:109]).rstrip() + '\n'
(OUT / 'app' / 'config.py').write_text(config_text, encoding='utf-8')
(OUT / 'app' / 'runtime.py').write_text('"""Shared aiogram Router instance."""\n\nfrom aiogram import Router\n\nrouter = Router()\n', encoding='utf-8')

for group, names in groups.items():
    if group == 'runtime':
        continue
    ordered_nodes = sorted((nodes[name] for name in names), key=lambda n: getattr(n, 'lineno'))
    referenced = set()
    for node in ordered_nodes:
        referenced |= refs(node)
    internal_import_lines = []
    if referenced & config_names:
        internal_import_lines.append('from app import config')
    deps: dict[str, set[str]] = {}
    for ref in referenced:
        dep_group = name_group.get(ref)
        if dep_group and dep_group not in {group, 'config'}:
            deps.setdefault(dep_group, set()).add(ref)
    for dep_group in sorted(deps, key=lambda g: module_path[g]):
        internal_import_lines.append(f"from {module_path[dep_group]} import {', '.join(sorted(deps[dep_group]))}")

    body_parts = []
    for node in ordered_nodes:
        text = node_text(node)
        if referenced & config_names:
            import io, tokenize
            toks = []
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.NAME and tok.string in config_names:
                    tok = tokenize.TokenInfo(tok.type, 'config.' + tok.string, tok.start, tok.end, tok.line)
                toks.append(tok)
            text = tokenize.untokenize(toks)
        body_parts.append(text)

    header = f'"""{DOCS[group]}"""\n\n'
    import_lines = sorted({IMPORT_MAP[name] for name in referenced if name in IMPORT_MAP})
    imports = ('\n'.join(import_lines) + '\n\n') if import_lines else ''
    if internal_import_lines:
        imports += '\n'.join(internal_import_lines) + '\n\n'
    content = header + imports + '\n\n'.join(body_parts).rstrip() + '\n'
    rel = module_path[group].replace('.', '/') + '.py'
    (OUT / rel).write_text(content, encoding='utf-8')

main_path = OUT / 'app' / 'main.py'
main_content = main_path.read_text(encoding='utf-8')
registration = '''\n# Import handlers for their router decorators.\nfrom app.handlers import admin as _admin_handlers\nfrom app.handlers import customization as _customization_handlers\nfrom app.handlers import decisions as _decision_handlers\nfrom app.handlers import form as _form_handlers\nfrom app.handlers import user as _user_handlers\n'''
main_content = main_content.replace('\nasync def main(', registration + '\nasync def main(', 1)
main_path.write_text(main_content, encoding='utf-8')

bot_facade = '''"""Compatibility entry point for the modular bot application.\n\nNew code should import from ``app`` modules directly. Existing deployment can keep\nrunning ``python bot.py``.\n"""\n\nimport asyncio\n\nfrom app.config import *  # noqa: F401,F403\nfrom app.database import *  # noqa: F401,F403\nfrom app.diagnostics import *  # noqa: F401,F403\nfrom app.questions import *  # noqa: F401,F403\nfrom app.services import *  # noqa: F401,F403\nfrom app.states import *  # noqa: F401,F403\nfrom app.ui import *  # noqa: F401,F403\nfrom app.runtime import router\n\nfrom app.database import _application_answers, _next_application_id, _remove_staff_messages\nfrom app.diagnostics import _build_error_payload, _error_update_context\nfrom app.states import _is_application_state, _state_matches\nfrom app.ui import _form_callback, _validate_form_session\n\nfrom app.handlers.admin import *  # noqa: F401,F403\nfrom app.handlers.customization import *  # noqa: F401,F403\nfrom app.handlers.decisions import *  # noqa: F401,F403\nfrom app.handlers.form import *  # noqa: F401,F403\nfrom app.handlers.user import *  # noqa: F401,F403\n\nfrom app.main import main\n\nif __name__ == "__main__":\n    asyncio.run(main())\n'''
(OUT / 'bot.py').write_text(bot_facade, encoding='utf-8')

test_path = OUT / 'tests' / 'test_logic.py'
if test_path.exists():
    test_text = test_path.read_text(encoding='utf-8')
    if 'from app import config' not in test_text:
        test_text = test_text.replace('import bot\n', 'import bot\nfrom app import config\n', 1)
    test_text = test_text.replace('bot.DB_PATH', 'config.DB_PATH')
    test_path.write_text(test_text, encoding='utf-8')

architecture = '''# Структура Telegram-бота\n\n`bot.py` теперь только совместимая точка входа. Основная логика находится в пакете `app/`.\n\n| Файл | Ответственность |\n|---|---|\n| `app/config.py` | `.env`, ID администраторов, путь БД, логирование |\n| `app/runtime.py` | единый `Router` aiogram |\n| `app/states.py` | FSM-состояния анкеты |\n| `app/questions.py` | вопросы, лимиты, варианты ответов, причины отказа |\n| `app/database.py` | SQLite, заявки, история, служебные настройки |\n| `app/diagnostics.py` | обработка исключений и traceback |\n| `app/services.py` | синхронизация карточек STAFF с данными БД |\n| `app/ui.py` | клавиатуры, тексты карточек, отображение шагов |\n| `app/handlers/user.py` | `/start`, кабинет, статус, история пользователя |\n| `app/handlers/form.py` | заполнение, назад, редактирование, отправка анкеты |\n| `app/handlers/decisions.py` | принятие/отклонение заявки |\n| `app/handlers/admin.py` | админ-панель, поиск, очистка и управление заявками |\n| `app/handlers/customization.py` | тексты вопросов, стили и Premium Emoji |\n| `app/main.py` | сборка приложения и polling |\n\n## Где исправлять типовые проблемы\n\n- вопрос или переход анкеты — `app/handlers/form.py` + `app/questions.py`;\n- кнопка/текст интерфейса — `app/ui.py`;\n- принятие/отказ — `app/handlers/decisions.py`;\n- админ-панель — `app/handlers/admin.py`;\n- кастомизация — `app/handlers/customization.py`;\n- сохранение/поиск заявки — `app/database.py`;\n- падение/traceback — `app/diagnostics.py`;\n- запуск и переменные окружения — `app/config.py`, `app/main.py`.\n\n## Правило зависимостей\n\nОбработчики могут использовать `database`, `ui`, `questions`, `states` и `services`. Низкоуровневые модули не должны импортировать handlers. Это уменьшает риск циклических импортов и делает локальные исправления безопаснее.\n\n## Проверка\n\nПосле изменений GitHub Actions компилирует и импортирует приложение и запускает `tests/test_logic.py` на Python 3.11 и 3.12.\n'''
(OUT / 'ARCHITECTURE.md').write_text(architecture, encoding='utf-8')

for p in sorted(OUT.rglob('*.py')):
    if '.git' in p.parts:
        continue
    compile(p.read_text(encoding='utf-8'), str(p), 'exec')
