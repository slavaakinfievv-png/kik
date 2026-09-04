# Структура Telegram-бота

`bot.py` теперь только совместимая точка входа. Основная логика находится в пакете `app/`.

| Файл | Ответственность |
|---|---|
| `app/config.py` | `.env`, ID администраторов, путь БД, логирование |
| `app/runtime.py` | единый `Router` aiogram |
| `app/states.py` | FSM-состояния анкеты |
| `app/questions.py` | вопросы, лимиты, варианты ответов, причины отказа |
| `app/database.py` | SQLite, заявки, история, служебные настройки |
| `app/diagnostics.py` | обработка исключений и traceback |
| `app/services.py` | синхронизация карточек STAFF с данными БД |
| `app/ui.py` | клавиатуры, тексты карточек, отображение шагов |
| `app/handlers/user.py` | `/start`, кабинет, статус, история пользователя |
| `app/handlers/form.py` | заполнение, назад, редактирование, отправка анкеты |
| `app/handlers/decisions.py` | принятие/отклонение заявки |
| `app/handlers/admin.py` | админ-панель, поиск, очистка и управление заявками |
| `app/handlers/customization.py` | тексты вопросов, стили и Premium Emoji |
| `app/main.py` | сборка приложения и polling |

## Где исправлять типовые проблемы

- вопрос или переход анкеты — `app/handlers/form.py` + `app/questions.py`;
- кнопка/текст интерфейса — `app/ui.py`;
- принятие/отказ — `app/handlers/decisions.py`;
- админ-панель — `app/handlers/admin.py`;
- кастомизация — `app/handlers/customization.py`;
- сохранение/поиск заявки — `app/database.py`;
- падение/traceback — `app/diagnostics.py`;
- запуск и переменные окружения — `app/config.py`, `app/main.py`.

## Правило зависимостей

Обработчики могут использовать `database`, `ui`, `questions`, `states` и `services`. Низкоуровневые модули не должны импортировать handlers. Это уменьшает риск циклических импортов и делает локальные исправления безопаснее.

`bot.py` сохранён как совместимый фасад, поэтому существующая команда запуска `python bot.py` остаётся рабочей.

## Проверка

После изменений GitHub Actions компилирует и импортирует приложение и запускает `tests/test_logic.py` на Python 3.11 и 3.12.
